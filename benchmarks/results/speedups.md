# CGOProf Optimization Benchmark Results

| Case | Baseline median (ms) | Optimized median (ms) | Speedup |
|---|---:|---:|---:|
| small_calls | 21.609 | 4.622 | 4.68x |
| conversion_copy | 19.325 | 8.044 | 2.40x |
| pointer_check | 9.655 | 6.473 | 1.49x |
| callback_pingpong | 11.863 | 5.365 | 2.21x |

Measured from prebuilt binaries; medians exclude Go build time.