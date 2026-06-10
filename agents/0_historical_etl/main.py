import time
import schedule
import logging
import concurrent.futures
from database import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent0_ETL")

def fetch_pbpstats():
    logger.info("Fetching data from pbpstats.com...")
    # Simulated fetch logic
    time.sleep(1)
    logger.info("pbpstats.com data fetched.")

def fetch_wnba_stats():
    logger.info("Fetching data from stats.wnba.com...")
    # Simulated fetch logic
    time.sleep(1)
    logger.info("stats.wnba.com data fetched.")

def calculate_rolling_baselines():
    logger.info("Recalculating rolling baselines (5/10-game USG%, Pace, O-Rtg/D-Rtg)...")
    # Simulated calculation
    time.sleep(1)
    logger.info("Rolling baselines updated in hoopstats_wnba.db.")

def nightly_etl_job():
    logger.info("Starting Nightly ETL Job...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_pbp = executor.submit(fetch_pbpstats)
        future_wnba = executor.submit(fetch_wnba_stats)

        # Calling .result() ensures any exceptions raised in the threads are propagated
        future_pbp.result()
        future_wnba.result()

    calculate_rolling_baselines()
    logger.info("Nightly ETL Job completed.")

def main():
    init_db()
    logger.info("Agent 0 (Historical ETL) started.")
    
    # Run once on startup for backfilling
    logger.info("Running initial backfill for 3 WNBA seasons...")
    nightly_etl_job()
    
    # Schedule to run at 4:00 AM CST (9:00 AM UTC)
    schedule.every().day.at("09:00").do(nightly_etl_job)
    
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
