from __future__ import annotations

import os
import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from cgoprof.cli import main


class InstrumenterTests(unittest.TestCase):
    def test_ast_instrumenter_rewrites_and_builds_cgo_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            out = tmp_path / "out"
            src.mkdir()
            (src / "go.mod").write_text("module example.com/autocgo\n\ngo 1.21\n", encoding="utf-8")
            (src / "main.go").write_text(
                textwrap.dedent(
                    """
                    package main

                    /*
                    #include <stdlib.h>
                    #include <string.h>

                    static int add_one(int x) {
                        return x + 1;
                    }

                    static void sink(void *p) {}

                    static const char *stable_string() {
                        return "hello";
                    }

                    static void *identity(void *p) {
                        return p;
                    }
                    */
                    import "C"

                    import "unsafe"

                    func main() {
                        s := "abc"
                        cs := C.CString(s)
                        defer C.free(unsafe.Pointer(cs))
                        x := int(C.add_one(C.int(1)))
                        data := []byte{1, 2, 3, 4}
                        cbuf := C.CBytes(data)
                        raw := C.malloc(C.size_t(16))
                        C.free(raw)
                        _ = C.GoString(C.stable_string())
                        _ = C.GoBytes(C.identity(cbuf), C.int(len(data)))
                        C.free(cbuf)
                        cbuf2 := makeCBuffer(data)
                        C.sink(cbuf2)
                        C.free(cbuf2)
                        ptr := unsafe.Pointer(&data[0])
                        C.sink(ptr)
                        C.sink(bufferPtr(data))
                        C.sink(passthrough(ptr))
                        _ = x
                    }

                    func makeCBuffer(data []byte) unsafe.Pointer {
                        return C.CBytes(data)
                    }
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (src / "ptrhelper.go").write_text(
                textwrap.dedent(
                    """
                    package main

                    import "unsafe"

                    func bufferPtr(data []byte) unsafe.Pointer {
                        return unsafe.Pointer(&data[0])
                    }

                    func passthrough(ptr unsafe.Pointer) unsafe.Pointer {
                        return ptr
                    }
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (src / "callback.go").write_text(
                textwrap.dedent(
                    """
                    package main

                    /*
                    typedef int callback_marker;
                    */
                    import "C"

                    //export goCallback
                    func goCallback(v C.int) {
                        _ = int(v)
                    }
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(main(["instrument", str(src), "--out", str(out)]), 0)

            rewritten = (out / "main.go").read_text(encoding="utf-8")
            callback = (out / "callback.go").read_text(encoding="utf-8")
            gomod = (out / "go.mod").read_text(encoding="utf-8")
            self.assertIn('prof "cgoprof/runtime_go/cgoprof"', rewritten)
            self.assertIn("prof.Conversion", rewritten)
            self.assertIn("prof.Memory", rewritten)
            self.assertIn("prof.BeginCall", rewritten)
            self.assertIn("prof.PointerCheck", rewritten)
            self.assertIn("prof.Callback", callback)
            self.assertIn("defer func()", rewritten)
            self.assertIn("replace cgoprof/runtime_go/cgoprof =>", gomod)

            env = os.environ.copy()
            env.setdefault("GOCACHE", "/private/tmp/go-build-cache")
            env["CGOPROF_OUT"] = str(tmp_path / "profile.jsonl")
            subprocess.run(["go", "run", "."], cwd=out, env=env, check=True)
            profile = (tmp_path / "profile.jsonl").read_text(encoding="utf-8")
            events = [json.loads(line) for line in profile.splitlines() if line.strip()]
            pointer_checks = [event for event in events if event["kind"] == "pointer_check"]
            malloc_events = [
                event for event in events
                if event["kind"] == "memory" and event["detail"].get("op") == "malloc"
            ]
            free_events = [
                event for event in events
                if event["kind"] == "memory" and event["detail"].get("op") == "free"
            ]
            self.assertIn('"kind":"conversion"', profile)
            self.assertIn('"kind":"cgo_call"', profile)
            self.assertIn('"kind":"pointer_check"', profile)
            self.assertIn('"op":"C.GoString"', profile)
            self.assertIn('"op":"C.GoBytes"', profile)
            self.assertGreaterEqual(len(malloc_events), 4)
            self.assertGreaterEqual(len(free_events), 4)
            self.assertEqual(len(pointer_checks), 3)


if __name__ == "__main__":
    unittest.main()
