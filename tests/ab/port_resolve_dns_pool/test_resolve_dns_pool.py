"""Unit tests for _DnsResolver's bounded worker-pool (_resolve_async).

resolve-dns over a huge crawler month (300k–2M distinct IPs) once
OOM-killed MariaDB on the 4.5 GB / no-swap host.  The working set was
already streamed to an on-disk temp table, but the per-batch resolver
still scheduled one asyncio Task per IP (gather over the whole 10K-IP
batch gated by a Semaphore), so peak async memory scaled with batch
size rather than concurrency.

The worker-pool rewrite caps live coroutine frames + in-flight c-ares
queries at `concurrency`.  These tests pin that guarantee (the memory
invariant) plus functional correctness, using a fake resolver so no
network / real DNS is involved.
"""
import sys, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _hz():
    import importlib, hzmetrics
    return importlib.reload(hzmetrics)


class _Rec:
    """Stand-in for aiodns' gethostbyaddr result (just needs .name)."""
    def __init__(self, name):
        self.name = name


class _FakeResolver:
    """Records max simultaneous in-flight queries so a test can assert
    the worker-pool never exceeds `concurrency`.  IPs ending in '.13'
    raise DNSError (the no-PTR path)."""
    def __init__(self, aiodns, asyncio):
        self._aiodns = aiodns
        self._asyncio = asyncio
        self.inflight = 0
        self.max_inflight = 0

    async def gethostbyaddr(self, ip):
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            # Yield long enough that every started worker is simultaneously
            # inside a query, so max_inflight reflects the real pool size.
            await self._asyncio.sleep(0.02)
            if ip.endswith(".13"):
                raise self._aiodns.error.DNSError("no PTR")
            return _Rec("HOST-" + ip.replace(".", "-"))
        finally:
            self.inflight -= 1

    def cancel(self):
        pass


class ResolverPoolTests(unittest.TestCase):

    def setUp(self):
        self.hz = _hz()
        try:
            import aiodns  # noqa: F401
        except ImportError:
            self.skipTest("aiodns not installed")

    def _make(self, concurrency):
        import asyncio, aiodns
        r = self.hz._DnsResolver(nameserver="system",
                                 concurrency=concurrency, timeout=1)
        r._resolver = _FakeResolver(aiodns, asyncio)
        return r

    def test_all_ips_resolved_unordered(self):
        r = self._make(concurrency=4)
        try:
            ips = [f"10.0.0.{i}" for i in range(20)]
            pairs = dict(r.resolve(ips))
        finally:
            r.close()
        self.assertEqual(set(pairs), set(ips))                 # all present
        self.assertEqual(pairs["10.0.0.1"], "HOST-10-0-0-1")   # resolved
        self.assertEqual(pairs["10.0.0.13"], "?")              # DNSError → "?"

    def test_concurrency_is_capped(self):
        # The memory invariant: never more than `concurrency` in-flight.
        r = self._make(concurrency=4)
        try:
            r.resolve([f"10.0.0.{i}" for i in range(50)])
            self.assertLessEqual(r._resolver.max_inflight, 4)
            self.assertGreater(r._resolver.max_inflight, 1)    # actually parallel
        finally:
            r.close()

    def test_workers_bounded_by_ip_count(self):
        # Fewer IPs than concurrency → at most len(ips) in flight.
        r = self._make(concurrency=16)
        try:
            r.resolve(["10.0.0.1", "10.0.0.2", "10.0.0.3"])
            self.assertLessEqual(r._resolver.max_inflight, 3)
        finally:
            r.close()

    def test_empty_batch(self):
        r = self._make(concurrency=4)
        try:
            self.assertEqual(r.resolve([]), [])
        finally:
            r.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
