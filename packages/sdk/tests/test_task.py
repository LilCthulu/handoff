"""Tests for SDK task framework — TaskRouter, TaskExecutor, TaskContext."""

import asyncio

import pytest

from handoff_sdk.task import TaskContext, TaskDefinition, TaskRouter, TaskExecutor


class TestTaskContext:
    def test_create(self):
        ctx = TaskContext(
            handoff_id="h-1",
            from_agent_id="agent-a",
            domain="hotels",
            action="search",
            input_data={"city": "Tokyo"},
        )
        assert ctx.handoff_id == "h-1"
        assert ctx.domain == "hotels"
        assert ctx.input_data == {"city": "Tokyo"}
        assert ctx.negotiation_id is None
        assert ctx.chain_id is None
        assert ctx.metadata == {}

    @pytest.mark.asyncio
    async def test_report_progress_with_fn(self):
        reported = []

        async def mock_progress(msg, pct):
            reported.append((msg, pct))

        ctx = TaskContext(
            handoff_id="h-1",
            from_agent_id="a",
            domain="d",
            action="a",
            input_data={},
            _progress_fn=mock_progress,
        )
        await ctx.report_progress("halfway", 0.5)
        assert reported == [("halfway", 0.5)]

    @pytest.mark.asyncio
    async def test_report_progress_without_fn(self):
        ctx = TaskContext(
            handoff_id="h-1",
            from_agent_id="a",
            domain="d",
            action="a",
            input_data={},
        )
        # Should not raise
        await ctx.report_progress("test", 0.0)


class TestTaskRouter:
    def test_register(self):
        router = TaskRouter()

        async def handler(ctx):
            return {"result": True}

        defn = router.register(
            domain="hotels",
            action="search",
            handler=handler,
            version="1.0.0",
        )
        assert defn.domain == "hotels"
        assert defn.action == "search"
        assert defn.version == "1.0.0"
        assert defn.timeout_seconds == 300.0

    def test_register_duplicate_raises(self):
        router = TaskRouter()

        async def handler(ctx):
            return {}

        router.register(domain="hotels", action="search", handler=handler)
        with pytest.raises(ValueError, match="already registered"):
            router.register(domain="hotels", action="search", handler=handler)

    def test_resolve_found(self):
        router = TaskRouter()

        async def handler(ctx):
            return {}

        router.register(domain="hotels", action="search", handler=handler)
        defn = router.resolve("hotels", "search")
        assert defn is not None
        assert defn.domain == "hotels"

    def test_resolve_not_found(self):
        router = TaskRouter()
        assert router.resolve("missing", "action") is None

    def test_definitions_list(self):
        router = TaskRouter()

        async def h1(ctx):
            return {}

        async def h2(ctx):
            return {}

        router.register(domain="hotels", action="search", handler=h1)
        router.register(domain="flights", action="book", handler=h2)
        assert len(router.definitions) == 2

    def test_register_with_max_concurrent_creates_semaphore(self):
        router = TaskRouter()

        async def handler(ctx):
            return {}

        router.register(domain="d", action="a", handler=handler, max_concurrent=5)
        assert "d:a" in router._semaphores
        assert router._semaphores["d:a"]._value == 5

    @pytest.mark.asyncio
    async def test_execute_success(self):
        router = TaskRouter()

        async def handler(ctx):
            return {"answer": 42}

        defn = router.register(domain="math", action="compute", handler=handler)
        ctx = TaskContext(
            handoff_id="h-1",
            from_agent_id="a",
            domain="math",
            action="compute",
            input_data={},
        )

        result = await router.execute(defn, ctx)
        assert result == {"answer": 42}

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        router = TaskRouter()

        async def slow_handler(ctx):
            await asyncio.sleep(10)
            return {}

        defn = router.register(
            domain="slow", action="work", handler=slow_handler, timeout_seconds=0.1
        )
        ctx = TaskContext(
            handoff_id="h-1",
            from_agent_id="a",
            domain="slow",
            action="work",
            input_data={},
        )

        with pytest.raises(asyncio.TimeoutError):
            await router.execute(defn, ctx)

    @pytest.mark.asyncio
    async def test_execute_handler_error(self):
        router = TaskRouter()

        async def bad_handler(ctx):
            raise ValueError("something broke")

        defn = router.register(domain="bad", action="fail", handler=bad_handler)
        ctx = TaskContext(
            handoff_id="h-1",
            from_agent_id="a",
            domain="bad",
            action="fail",
            input_data={},
        )

        with pytest.raises(ValueError, match="something broke"):
            await router.execute(defn, ctx)

    @pytest.mark.asyncio
    async def test_execute_with_semaphore(self):
        router = TaskRouter()
        concurrent_count = 0
        max_observed = 0

        async def handler(ctx):
            nonlocal concurrent_count, max_observed
            concurrent_count += 1
            max_observed = max(max_observed, concurrent_count)
            await asyncio.sleep(0.05)
            concurrent_count -= 1
            return {}

        defn = router.register(
            domain="limited", action="work", handler=handler, max_concurrent=2
        )

        tasks = []
        for i in range(5):
            ctx = TaskContext(
                handoff_id=f"h-{i}",
                from_agent_id="a",
                domain="limited",
                action="work",
                input_data={},
            )
            tasks.append(router.execute(defn, ctx))

        await asyncio.gather(*tasks)
        assert max_observed <= 2

    @pytest.mark.asyncio
    async def test_track_and_untrack(self):
        router = TaskRouter()

        async def noop():
            pass

        task = asyncio.create_task(noop())
        router.track("h-1", task)
        assert "h-1" in router._running
        router.untrack("h-1")
        assert "h-1" not in router._running
        await task

    @pytest.mark.asyncio
    async def test_cancel_all(self):
        router = TaskRouter()

        async def forever():
            await asyncio.sleep(999)

        task = asyncio.create_task(forever())
        router.track("h-1", task)

        await router.cancel_all()
        assert len(router._running) == 0
        # Give the event loop a tick to process the cancellation
        await asyncio.sleep(0)
        assert task.cancelled()


class TestTaskExecutor:
    @pytest.mark.asyncio
    async def test_register_capabilities(self):
        router = TaskRouter()

        async def handler(ctx):
            return {}

        router.register(
            domain="hotels",
            action="search",
            handler=handler,
            description="Search hotels",
        )

        calls = []

        class FakeClient:
            async def post(self, path, data=None, **kwargs):
                calls.append((path, data))

        executor = TaskExecutor(router, FakeClient(), "agent-1")
        await executor.register_capabilities()

        assert len(calls) == 1
        assert calls[0][0] == "/api/v1/capabilities"
        assert calls[0][1]["domain"] == "hotels"
        assert calls[0][1]["action"] == "search"

    @pytest.mark.asyncio
    async def test_handle_challenge_with_matching_handler(self):
        router = TaskRouter()

        async def handler(ctx):
            assert ctx.metadata.get("is_challenge") is True
            return {"answer": "correct"}

        router.register(domain="math", action="add", handler=handler)

        responses = {}

        class FakeClient:
            async def post(self, path, data=None, **kwargs):
                responses[path] = data

        executor = TaskExecutor(router, FakeClient(), "agent-1")

        await executor.handle_challenge({
            "id": "c-1",
            "domain": "math",
            "action": "add",
            "challenge_input": {"x": 1, "y": 2},
            "issued_by": "verifier",
        })

        assert "/api/v1/challenges/c-1/respond" in responses
        assert responses["/api/v1/challenges/c-1/respond"]["response"] == {"answer": "correct"}

    @pytest.mark.asyncio
    async def test_handle_challenge_no_matching_handler(self):
        router = TaskRouter()

        class FakeClient:
            async def post(self, path, data=None, **kwargs):
                pytest.fail("Should not call API when no handler matches")

        executor = TaskExecutor(router, FakeClient(), "agent-1")

        # Should not raise, just log warning
        await executor.handle_challenge({
            "id": "c-2",
            "domain": "unknown",
            "action": "mystery",
        })

    @pytest.mark.asyncio
    async def test_stop(self):
        router = TaskRouter()

        async def handler(ctx):
            return {}

        router.register(domain="d", action="a", handler=handler)

        class FakeClient:
            pass

        executor = TaskExecutor(router, FakeClient(), "agent-1")
        executor._running = True

        await executor.stop()
        assert not executor._running
