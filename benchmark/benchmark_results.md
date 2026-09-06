# HandsOnAid Mobile Deployment Benchmarks

This document tracks the latency and throughput of the fine-tuned Gemma 2B models under simulated mobile hardware constraints (using Docker cgroups).

## Benchmark Configuration
- **Dataset:** 5 random questions sampled from `eval_bank_v2.json`
- **Context Size:** 512 tokens
- **Inference Engine:** `llama-cpp-python` (CPU only)

---

## 1. Mid-Range Mobile Profile
**Constraints:** 4.0 CPU Cores, 4GB RAM

| Model | Size | Model Load Time | Avg Latency (TTFT) | Avg Throughput | Avg Total Answer Time |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `merged_4bit_Q4_K_M` | 1.55 GB | 12.55 seconds | 0.67 seconds | 3.14 tokens/sec | 12.99 seconds |
| `merged_4bit_Q8_0` | 2.55 GB | 20.41 seconds | 1.31 seconds | 1.80 tokens/sec | 26.61 seconds |
| `merged_8bit_Q4_K_M` | 1.55 GB | 15.43 seconds | 1.59 seconds | 3.07 tokens/sec | 12.31 seconds |
| `merged_8bit_Q8_0` | 2.55 GB | 19.17 seconds | 1.43 seconds | 2.50 tokens/sec | 15.69 seconds |

*(Note: Speeds are measured inside Docker on Windows/WSL2; actual bare-metal Android/iOS devices utilizing mobile NPUs or Apple Silicon will achieve significantly faster speeds.)*
