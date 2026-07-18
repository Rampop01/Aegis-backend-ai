import os
from datetime import timedelta
import psycopg2
from psycopg2.extras import execute_values, Json, RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    """Establishes a connection to the PostgreSQL/TimescaleDB database."""
    return psycopg2.connect(DATABASE_URL)

def save_fx_rates(rates):
    """
    Saves a list of FX rates to the database.
    Each rate should be a tuple (timestamp, pair, rate, source).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            execute_values(cur,
                "INSERT INTO fx_rates (timestamp, pair, rate, source) VALUES %s ON CONFLICT DO NOTHING",
                rates
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error saving FX rates: {e}")
        raise
    finally:
        conn.close()

def save_sentiment_data(records):
    """
    Saves a list of sentiment records to the database.
    Each record should be a tuple (timestamp, source, keyword, content, sentiment_score).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            execute_values(cur,
                "INSERT INTO sentiment_data (timestamp, source, keyword, content, sentiment_score) VALUES %s",
                records
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error saving sentiment data: {e}")
        raise
    finally:
        conn.close()

def get_fx_rate_series(pair, start_date, end_date):
    """
    Returns [(timestamp, rate), ...] for the given pair between start_date and
    end_date (inclusive of both calendar days), ordered by timestamp ascending.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT "timestamp", rate
                FROM fx_rates
                WHERE pair = %s AND "timestamp" >= %s AND "timestamp" < %s
                ORDER BY "timestamp" ASC
                """,
                (pair, start_date, end_date + timedelta(days=1)),
            )
            return cur.fetchall()
    finally:
        conn.close()

def save_backtest_result(strategy_name, pair, start_date, end_date, data_points_used,
                          params, strategy_metrics, baseline_metrics, comparison):
    """
    Persists a backtest report and returns its generated {id, created_at}.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO backtest_results
                    (strategy_name, pair, start_date, end_date, data_points_used,
                     params, strategy_metrics, baseline_metrics, comparison)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
                """,
                (
                    strategy_name,
                    pair,
                    start_date,
                    end_date,
                    data_points_used,
                    Json(params),
                    Json(strategy_metrics),
                    Json(baseline_metrics),
                    Json(comparison),
                ),
            )
            result = cur.fetchone()
        conn.commit()
        return result
    except Exception as e:
        conn.rollback()
        print(f"Error saving backtest result: {e}")
        raise
    finally:
        conn.close()

def list_backtest_results(pair=None, strategy_name=None, limit=20, offset=0):
    """
    Returns stored backtest reports ordered by most recent first, optionally
    filtered by pair and/or strategy_name.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            filters = []
            values = []
            if pair:
                filters.append('pair = %s')
                values.append(pair)
            if strategy_name:
                filters.append('strategy_name = %s')
                values.append(strategy_name)

            where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
            values.extend([limit, offset])

            cur.execute(
                f"""
                SELECT id, created_at, strategy_name, pair, start_date, end_date,
                       data_points_used, params, strategy_metrics, baseline_metrics, comparison
                FROM backtest_results
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                values,
            )
            return cur.fetchall()
    finally:
        conn.close()
