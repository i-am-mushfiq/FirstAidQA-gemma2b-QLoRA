FROM python:3.11-slim

WORKDIR /app

# Install dependencies required for llama-cpp-python compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    && rm -rf /var/lib/apt/lists/*

# Install llama-cpp-python
RUN CMAKE_ARGS="-DGGML_RPC=OFF" pip install --no-cache-dir llama-cpp-python

COPY run_benchmark.py .

CMD ["python", "run_benchmark.py"]
