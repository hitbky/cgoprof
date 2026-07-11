from __future__ import annotations

import unittest

from cgoprof.graph import InteractionGraph
from cgoprof.models import EventKind, ProfileEvent
from cgoprof.rules import run_rules
from cgoprof.scanner import scan_project


class RuleTests(unittest.TestCase):
    def test_small_call_detector_fires(self) -> None:
        graph = InteractionGraph()
        for _ in range(1000):
            graph.add_event(
                ProfileEvent(
                    kind=EventKind.CGO_CALL,
                    site_id="small",
                    duration_ns=30_000,
                    function="tiny",
                    detail={"boundary_ns": 20_000, "c_work_ns": 10_000},
                )
            )
        self.assert_rules(graph, {"small-call-detector"})

    def test_small_call_detector_fires_without_boundary_samples(self) -> None:
        graph = InteractionGraph()
        for _ in range(1000):
            graph.add_event(
                ProfileEvent(
                    kind=EventKind.CGO_CALL,
                    site_id="auto-small",
                    duration_ns=30_000,
                    function="tiny",
                    detail={"c_symbol": "tiny"},
                )
            )
        self.assert_rules(graph, {"small-call-detector"})

    def test_conversion_copy_detector_fires(self) -> None:
        graph = InteractionGraph()
        for _ in range(100):
            graph.add_event(
                ProfileEvent(
                    kind=EventKind.CONVERSION,
                    site_id="copy",
                    bytes=1024,
                    detail={"op": "C.CString"},
                )
            )
            graph.add_event(
                ProfileEvent(
                    kind=EventKind.MEMORY,
                    site_id="copy",
                    bytes=1024,
                    detail={"op": "memcpy"},
                )
            )
        self.assert_rules(graph, {"conversion-copy-detector"})

    def test_pointer_check_overhead_detector_fires(self) -> None:
        graph = InteractionGraph()
        for _ in range(100):
            graph.add_event(
                ProfileEvent(
                    kind=EventKind.POINTER_CHECK,
                    site_id="ptr",
                    duration_ns=100_000,
                )
            )
        self.assert_rules(graph, {"pointer-check-overhead-detector"})

    def test_callback_pingpong_detector_fires(self) -> None:
        graph = InteractionGraph()
        for _ in range(1000):
            graph.add_event(
                ProfileEvent(
                    kind=EventKind.CALLBACK,
                    site_id="cb",
                    duration_ns=100,
                    source="c_loop",
                )
            )
        self.assert_rules(graph, {"callback-pingpong-detector"})

    def test_inbound_copy_detector_fires(self) -> None:
        graph = InteractionGraph()
        for _ in range(1000):
            graph.add_event(
                ProfileEvent(
                    kind=EventKind.CONVERSION,
                    site_id="inbound-copy",
                    bytes=128,
                    detail={"op": "C.GoBytes"},
                )
            )
        self.assert_rules(graph, {"inbound-copy-detector"})

    def assert_rules(self, graph: InteractionGraph, expected: set[str]) -> None:
        names = {finding.rule for finding in run_rules(graph)}
        self.assertEqual(names, expected)


class ScannerTests(unittest.TestCase):
    def test_scanner_finds_wrapped_cgo_sites(self) -> None:
        callsites, _ = scan_project("examples/small_calls")
        self.assertTrue(any(site.site_id == "small-add-one" for site in callsites))


if __name__ == "__main__":
    unittest.main()
