import time
import sys
import os
from llama_cpp import Llama

def run_benchmark(model_path):
    print(f"Loading model: {model_path}")
    
    # Initialize model
    # We restrict threads to match the available CPU cores in the container
    load_start = time.time()
    llm = Llama(
        model_path=model_path,
        n_ctx=512,  # context size
        n_threads=int(os.cpu_count() or 1), 
        verbose=False
    )
    load_time = time.time() - load_start
    print(f"Model Load Time: {load_time:.2f} seconds")
    
    import json
    import random
    
    # Load 5 random questions from eval bank
    try:
        with open("eval_bank_v2.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            # Filter to just get the 'question' fields
            all_questions = [item["question"] for item in data if "question" in item]
            # Pick 5 random ones
            prompts = random.sample(all_questions, min(5, len(all_questions)))
    except Exception as e:
        print(f"Could not load eval bank: {e}. Falling back to default questions.")
        prompts = [
            "What are the symptoms of a mild concussion?",
            "How do I treat a minor burn at home?",
            "Explain the difference between ibuprofen and acetaminophen."
        ]
    
    results = []
    
    print("\n--- Starting Benchmark ---")
    for i, prompt in enumerate(prompts):
        print(f"\nPrompt {i+1}: {prompt}")
        
        # We use a custom generation loop to easily measure TTFT
        start_time = time.time()
        
        # Start streaming response
        stream = llm.create_completion(
            prompt, 
            max_tokens=64, 
            stream=True,
            temperature=0.1
        )
        
        ttft = None
        generated_tokens = 0
        
        for output in stream:
            if ttft is None:
                # Time to first token
                ttft = time.time() - start_time
            generated_tokens += 1
            
        total_time = time.time() - start_time
        
        # Calculate tokens per second (excluding the TTFT wait time for pure generation speed)
        gen_time = total_time - ttft
        tps = (generated_tokens - 1) / gen_time if gen_time > 0 and generated_tokens > 1 else 0
        
        print(f"  Time to First Token (TTFT - Latency): {ttft:.2f} seconds")
        print(f"  Generation Speed (Throughput): {tps:.2f} tokens/sec")
        print(f"  Total Tokens Generated: {generated_tokens}")
        print(f"  Total Time for Complete Answer: {total_time:.2f} seconds")
        
        results.append({
            "ttft": ttft,
            "tps": tps,
            "total_time": total_time
        })
        
    # Print average summary
    avg_ttft = sum(r['ttft'] for r in results) / len(results)
    avg_tps = sum(r['tps'] for r in results) / len(results)
    avg_total = sum(r['total_time'] for r in results) / len(results)
    
    print("\n==============================")
    print("      BENCHMARK SUMMARY       ")
    print("==============================")
    print(f"Model Load Time: {load_time:.2f} s")
    print(f"Average TTFT (Latency): {avg_ttft:.2f} s")
    print(f"Average Speed (Throughput): {avg_tps:.2f} t/s")
    print(f"Average Total Answer Time: {avg_total:.2f} s")
    print("==============================")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_benchmark.py /path/to/model.gguf")
        sys.exit(1)
        
    run_benchmark(sys.argv[1])
