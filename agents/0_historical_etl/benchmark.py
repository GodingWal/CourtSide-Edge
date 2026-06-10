import time
from main import nightly_etl_job

def run_benchmark():
    start = time.time()
    nightly_etl_job()
    return time.time() - start

if __name__ == "__main__":
    elapsed = run_benchmark()
    print(f"Elapsed time: {elapsed:.2f} seconds")
