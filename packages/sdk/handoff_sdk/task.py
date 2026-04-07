"""Task execution framework — the @agent.task decorator.

This is the developer experience layer. Instead of manually handling
WebSocket messages, polling for handoffs, and submitting results,
agents declare task handlers with a decorator and the framework
does the rest:

    @agent.task(domain="hotels", action="search")
    async def search_hotels(ctx: TaskContext) -> dict:
        return {"hotels": [...]}

    agent.run()

The framework handles:
- Capability contract registration on the server
- Incoming handoff detection and routing
- Task execution with timeout protection
- Result submission and handoff completion
- Challenge auto-response (proof-of-competence)
- Error handling with automatic failure reporting
"""

import asyncio
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

import structlog

logger = structlog.get_logger()


@dataclass
class TaskContext:
    """The context passed to every task handler.

    Contains the handoff details, input data, and methods for
    reporting progress. This is the only thing a task author
    needs to understand.
    """

    handoff_id: str
    from_agent_id: str
    domain: str
    action: str
    input_data: dict[str, Any]
    negotiation_id: str | None = None
    chain_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Set by the framework — not for direct use
    _progress_fn: Callable[[str, float], Awaitable[None]] | None = field(
        default=None, repr=False
    )

    async def report_progress(self, message: str, percent: float = 0.0) -> None:
        """Report progress back to the delegating agent.

        Args:
            message: Human-readable progress description.
            percent: Completion percentage (0.0 to 1.0).
        """
        if self._progress_fn:
            await self._progress_fn(message, percent)


@dataclass
class TaskDefinition:
    """Internal representation of a registered task handler."""

    domain: str
    action: str
    handler: Callable[[TaskContext], Awaitable[dict[str, Any]]]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    version: str
    max_latency_ms: int | None
    availability_target: float | None
    max_concurrent: int | None
    description: str | None
    constraints: dict[str, Any]
    examples: list[dict[str, Any]]
    timeout_seconds: float


class TaskRouter:
    """Routes incoming handoffs to registered task handlers.

    The TaskRouter is the internal engine that the HandoffAgent
    delegates to. It maintains the registry of task definitions,
    matches incoming work to handlers, and manages execution.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskDefinition] = {}  # "domain:action" -> TaskDefinition
        self._running: dict[str, asyncio.Task] = {}  # handoff_id -> asyncio.Task
        self._semaphores: dict[str, asyncio.Semaphore] = {}  # "domain:action" -> Semaphore

    def register(
        self,
        domain: str,
        action: str,
        handler: Callable[[TaskContext], Awaitable[dict[str, Any]]],
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        version: str = "1.0.0",
        max_latency_ms: int | None = None,
        availability_target: float | None = None,
        max_concurrent: int | None = None,
        description: str | None = None,
        constraints: dict[str, Any] | None = None,
        examples: list[dict[str, Any]] | None = None,
        timeout_seconds: float = 300.0,
    ) -> TaskDefinition:
        """Register a task handler for a domain+action pair."""
        key = f"{domain}:{action}"
        if key in self._tasks:
            raise ValueError(f"Task handler already registered for {key}")

        defn = TaskDefinition(
            domain=domain,
            action=action,
            handler=handler,
            input_schema=input_schema or {},
            output_schema=output_schema or {},
            version=version,
            max_latency_ms=max_latency_ms,
            availability_target=availability_target,
            max_concurrent=max_concurrent,
            description=description,
            constraints=constraints or {},
            examples=examples or [],
            timeout_seconds=timeout_seconds,
        )
        self._tasks[key] = defn

        if max_concurrent:
            self._semaphores[key] = asyncio.Semaphore(max_concurrent)

        logger.info("task_registered", domain=domain, action=action, version=version)
        return defn

    @property
    def definitions(self) -> list[TaskDefinition]:
        return list(self._tasks.values())

    def resolve(self, domain: str, action: str) -> TaskDefinition | None:
        """Find the task handler for a domain+action pair."""
        return self._tasks.get(f"{domain}:{action}")

    async def execute(
        self,
        defn: TaskDefinition,
        ctx: TaskContext,
    ) -> dict[str, Any]:
        """Execute a task handler with timeout and concurrency control.

        Returns the handler's result dict on success.
        Raises on timeout or handler error.
        """
        key = f"{defn.domain}:{defn.action}"
        sem = self._semaphores.get(key)

        async def _run() -> dict[str, Any]:
            start = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    defn.handler(ctx),
                    timeout=defn.timeout_seconds,
                )
                elapsed_ms = (time.monotonic() - start) * 1000
                logger.info(
                    "task_completed",
                    domain=defn.domain,
                    action=defn.action,
                    handoff_id=ctx.handoff_id,
                    elapsed_ms=round(elapsed_ms, 2),
                )
                return result
            except asyncio.TimeoutError:
                elapsed_ms = (time.monotonic() - start) * 1000
                logger.warning(
                    "task_timeout",
                    domain=defn.domain,
                    action=defn.action,
                    handoff_id=ctx.handoff_id,
                    timeout=defn.timeout_seconds,
                    elapsed_ms=round(elapsed_ms, 2),
                )
                raise
            except Exception:
                elapsed_ms = (time.monotonic() - start) * 1000
                logger.exception(
                    "task_error",
                    domain=defn.domain,
                    action=defn.action,
                    handoff_id=ctx.handoff_id,
                    elapsed_ms=round(elapsed_ms, 2),
                )
                raise

        if sem:
            async with sem:
                return await _run()
        return await _run()

    def track(self, handoff_id: str, task: asyncio.Task) -> None:
        """Track a running task for cancellation support."""
        self._running[handoff_id] = task

    def untrack(self, handoff_id: str) -> None:
        """Remove a completed task from tracking."""
        self._running.pop(handoff_id, None)

    async def cancel_all(self) -> None:
        """Cancel all running tasks (used during shutdown)."""
        for hid, task in list(self._running.items()):
            if not task.done():
                task.cancel()
                logger.info("task_cancelled", handoff_id=hid)
        self._running.clear()


class TaskExecutor:
    """Bridges the task router with the Handoff client.

    This is the glue that:
    1. Registers capability contracts on the server
    2. Listens for incoming handoffs
    3. Routes them to the right handler
    4. Submits results or reports failures
    """

    def __init__(self, router: TaskRouter, client: Any, agent_id: str) -> None:
        self._router = router
        self._client = client
        self._agent_id = agent_id
        self._poll_interval = 5.0
        self._running = False

    async def register_capabilities(self) -> None:
        """Register all task definitions as capability contracts on the server."""
        for defn in self._router.definitions:
            try:
                await self._client.post(
                    "/api/v1/capabilities",
                    {
                        "domain": defn.domain,
                        "action": defn.action,
                        "version": defn.version,
                        "input_schema": defn.input_schema,
                        "output_schema": defn.output_schema,
                        "max_latency_ms": defn.max_latency_ms,
                        "availability_target": defn.availability_target,
                        "max_concurrent": defn.max_concurrent,
                        "constraints": defn.constraints,
                        "description": defn.description,
                        "examples": defn.examples,
                    },
                )
                logger.info(
                    "capability_registered_on_server",
                    domain=defn.domain,
                    action=defn.action,
                )
            except Exception:
                logger.exception(
                    "capability_registration_failed",
                    domain=defn.domain,
                    action=defn.action,
                )

    async def poll_for_handoffs(self) -> None:
        """Poll for incoming handoffs assigned to this agent.

        In production, WebSocket push replaces polling. This is the
        fallback for reliability — belt and suspenders.
        """
        self._running = True
        while self._running:
            try:
                handoffs = await self._client.get(
                    "/api/v1/handoffs",
                    params={"to_agent_id": self._agent_id, "status": "initiated"},
                )
                if isinstance(handoffs, list):
                    for h in handoffs:
                        await self._handle_handoff(h)
            except Exception:
                logger.exception("handoff_poll_error")

            await asyncio.sleep(self._poll_interval)

    async def handle_challenge(self, challenge_data: dict[str, Any]) -> None:
        """Auto-respond to a capability challenge using the registered handler.

        If the agent has a task handler for the challenged domain+action,
        execute it against the challenge input and submit the response.
        """
        challenge_id = challenge_data["id"]
        domain = challenge_data["domain"]
        action = challenge_data["action"]

        defn = self._router.resolve(domain, action)
        if not defn:
            logger.warning(
                "challenge_no_handler",
                challenge_id=challenge_id,
                domain=domain,
                action=action,
            )
            return

        ctx = TaskContext(
            handoff_id=f"challenge:{challenge_id}",
            from_agent_id=challenge_data.get("issued_by", ""),
            domain=domain,
            action=action,
            input_data=challenge_data.get("challenge_input", {}),
            metadata={"is_challenge": True},
        )

        try:
            result = await self._router.execute(defn, ctx)
            await self._client.post(
                f"/api/v1/challenges/{challenge_id}/respond",
                {"response": result},
            )
            logger.info("challenge_auto_responded", challenge_id=challenge_id, domain=domain)
        except Exception:
            logger.exception("challenge_auto_response_failed", challenge_id=challenge_id)

    async def poll_for_challenges(self) -> None:
        """Poll for pending capability challenges and auto-respond."""
        while self._running:
            try:
                challenges = await self._client.get("/api/v1/challenges/pending")
                if isinstance(challenges, list):
                    for c in challenges:
                        await self.handle_challenge(c)
            except Exception:
                logger.exception("challenge_poll_error")

            await asyncio.sleep(self._poll_interval * 2)  # less aggressive than handoff polling

    async def _handle_handoff(self, handoff_data: dict[str, Any]) -> None:
        """Route an incoming handoff to the appropriate task handler."""
        handoff_id = str(handoff_data["id"])
        context = handoff_data.get("context", {})
        domain = context.get("domain", "")
        action = context.get("action", "")

        defn = self._router.resolve(domain, action)
        if not defn:
            logger.warning(
                "handoff_no_handler",
                handoff_id=handoff_id,
                domain=domain,
                action=action,
            )
            return

        # Don't double-handle
        if handoff_id in self._router._running:
            return

        # Build progress reporter
        async def _report_progress(message: str, percent: float) -> None:
            try:
                await self._client.post(
                    f"/api/v1/progress/handoffs/{handoff_id}/update",
                    {"phase": message, "progress": percent, "message": message},
                )
            except Exception:
                logger.warning("progress_report_failed", handoff_id=handoff_id)

        ctx = TaskContext(
            handoff_id=handoff_id,
            from_agent_id=str(handoff_data.get("from_agent_id", "")),
            domain=domain,
            action=action,
            input_data=context.get("input", context),
            negotiation_id=str(handoff_data["negotiation_id"]) if handoff_data.get("negotiation_id") else None,
            chain_id=str(handoff_data["chain_id"]) if handoff_data.get("chain_id") else None,
            metadata=context.get("metadata", {}),
            _progress_fn=_report_progress,
        )

        async def _run_and_complete() -> None:
            try:
                # Mark as in_progress
                await self._client.patch(
                    f"/api/v1/handoffs/{handoff_id}/status",
                    {"status": "in_progress"},
                )

                # Execute the handler
                result = await self._router.execute(defn, ctx)

                # Submit result
                await self._client.post(
                    f"/api/v1/handoffs/{handoff_id}/result",
                    {"result": result},
                )

                logger.info("handoff_completed", handoff_id=handoff_id, domain=domain, action=action)

            except asyncio.TimeoutError:
                await self._client.patch(
                    f"/api/v1/handoffs/{handoff_id}/status",
                    {"status": "failed"},
                )
                logger.warning("handoff_failed_timeout", handoff_id=handoff_id)

            except Exception:
                tb = traceback.format_exc()
                try:
                    await self._client.patch(
                        f"/api/v1/handoffs/{handoff_id}/status",
                        {"status": "failed"},
                    )
                except Exception:
                    pass
                logger.exception("handoff_failed_error", handoff_id=handoff_id, traceback=tb)

            finally:
                self._router.untrack(handoff_id)

        task = asyncio.create_task(_run_and_complete())
        self._router.track(handoff_id, task)

    async def start(self) -> None:
        """Start polling for handoffs and challenges."""
        self._running = True
        await asyncio.gather(
            self.poll_for_handoffs(),
            self.poll_for_challenges(),
        )

    async def stop(self) -> None:
        """Stop the executor and cancel running tasks."""
        self._running = False
        await self._router.cancel_all()
