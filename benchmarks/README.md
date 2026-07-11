# CGOProf Optimization Benchmarks

This directory contains paired baseline and optimized programs for the four
inefficiency classes detected by CGOProf.

Run all benchmarks:

```bash
python3 benchmarks/run_benchmarks.py
```

Outputs:

- `benchmarks/results/speedups.json`
- `benchmarks/results/speedups.md`

The benchmark programs are separate from `examples/`:

- `examples/` keeps profiler instrumentation and proves CGOProf can detect the
  inefficiency.
- `benchmarks/` removes profiler instrumentation and measures the benefit of the
  corresponding rewrite.

## Rewrites

- `small_calls`: replace many `C.add_one` calls with one batched C function.
- `conversion_copy`: cache a stable `C.CString` instead of allocating/freeing it
  inside the hot loop.
- `pointer_check`: replace many calls that pass a Go slice pointer with one
  batched native call.
- `callback_pingpong`: replace many C -> Go callbacks with one C-side aggregate.
