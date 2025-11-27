import time
import statistics
import requests
import os

PROM_URL = os.getenv("PROM_URL", "http://localhost:9090")
ES_URL   = os.getenv("ES_URL", "http://localhost:9200")

NUM_RUNS = 5
SLEEP_BETWEEN = 0.5  # seconds


QUERIES = [
    {
        "name": "Q1_avg_status_code_1_host",
        "promql": """avg by (status_code) (avg_over_time(http_requests_qps{exported_instance="inst-00004"}[15m]))""",
        "esql": """
            TS metrics-http
            | WHERE  @timestamp >= NOW() - 15 MINUTES
              AND @timestamp <= NOW()
              AND instance == "inst-00004"
            | STATS avg_qps = AVG(http_requests_qps) BY status_code
""",
    },
     {
        "name": "Q2_avg_status_code_100_hosts_wildcard",
        "skip": False,
        # Average over time + average across series
        "promql": """avg by (status_code) (avg_over_time(http_requests_qps{exported_instance=~"inst-001.*"}[5m]))""",
        "range_duration": "1h",
        "step": "5m",
        "esql": """
            TS metrics-http
            | WHERE @timestamp >= NOW() - 60 MINUTES 
              AND @timestamp <= NOW() 
              AND instance LIKE "inst-001%"
            | EVAL bucket = DATE_TRUNC(5 MINUTES, @timestamp)
            | STATS avg_qps = AVG(http_requests_qps) BY bucket, status_code
""",
    },
    {
        "name": "Q3_avg_no_filter_sorted",
        "skip": False,
        "promql": """sort_desc(last_over_time(http_requests_qps[5m]))""",
        "range_duration": "1h",
        "step": "5m",
        "esql": """
TS metrics-http
| WHERE @timestamp >= NOW() - 60 MINUTES 
  AND @timestamp <= NOW()
| EVAL bucket = DATE_TRUNC(5 MINUTES, @timestamp)
| STATS avg_qps = AVG(http_requests_qps) BY bucket, instance, status_code, method, job
| SORT avg_qps DESC
""",
    },
    {
        "name": "Q4_avg_status_code_4xx_5xx_wildcard",
        "skip": True,
        "promql": """avg by (exported_instance) (avg_over_time(http_requests_qps[5m]))""",
        "range_duration": "4h",
        "step": "5m",
        "esql": """
            TS metrics-*
 | WHERE @timestamp >= NOW() - 240 MINUTES AND @timestamp <= NOW() 
 | STATS AVG(AVG_OVER_TIME(http_requests_qps)) BY instance, TBUCKET(5m)
""",
    }
]


def bench_prom(query):
    latencies = []
    first_result = None

    use_range = "range_duration" in query and "step" in query
    # Use longer timeout for range queries
    timeout = 120 if use_range else 15

    for i in range(NUM_RUNS):
        if use_range:
            duration_seconds = parse_duration(query["range_duration"])
            end_ts = time.time()
            start_ts = end_ts - duration_seconds
            step_seconds = parse_duration(query["step"])
            params = {
                "query": query["promql"],
                "start": f"{start_ts}",
                "end": f"{end_ts}",
                "step": f"{step_seconds}",
            }
            url = f"{PROM_URL}/api/v1/query_range"
        else:
            params = {
                "query": query["promql"],
                "time": str(time.time()),
            }
            url = f"{PROM_URL}/api/v1/query"

        t0 = time.time()
        r = requests.get(url, params=params, timeout=timeout)
        t1 = time.time()
        if r.status_code != 200:
            print(f"[Prom][{query['name']}] error:", r.status_code, r.text[:200])
        else:
            if i == 0:
                first_result = r.json()
        latencies.append((t1 - t0) * 1000.0)
        time.sleep(SLEEP_BETWEEN)
    return latencies, first_result


def parse_duration(duration_str: str) -> int:
    """Convert duration strings like 1m, 5m, 1h into seconds."""
    units = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
    }
    if duration_str[-1].isdigit():
        return int(duration_str)
    unit = duration_str[-1].lower()
    value = int(duration_str[:-1])
    return value * units.get(unit, 1)


def bench_esql(query):
    latencies = []
    first_result = None
    payload = {"query": query["esql"]}
    # Use longer timeout for queries with range_duration (likely large queries)
    timeout = 120 if "range_duration" in query else 15
    for i in range(NUM_RUNS):
        t0 = time.time()
        r = requests.post(f"{ES_URL}/_query?format=json", json=payload, timeout=timeout)
        t1 = time.time()
        if r.status_code != 200:
            print(f"[ES|QL][{query['name']}] error:", r.status_code, r.text[:200])
        else:
            if i == 0:
                first_result = r.json()
        latencies.append((t1 - t0) * 1000.0)
        time.sleep(SLEEP_BETWEEN)
    return latencies, first_result


def format_prom_result(result):
    """Format Prometheus result in compact form"""
    if not result or result.get("status") != "success":
        return "(Error or no data)"
    
    data = result.get("data", {})
    results = data.get("result", [])
    result_type = data.get("resultType", "")
    
    if not results:
        return "(No results)"
    
    lines = []
    for r in results:
        metric = r.get("metric", {})
        
        # Format labels
        labels = ", ".join([f"{k}={v}" for k, v in metric.items() if k != "__name__"])
        label_str = f"{{{labels}}}" if labels else ""
        
        # Handle both instant queries (value) and range queries (values)
        if result_type == "matrix" or "values" in r:
            # Range query: show last value from the series
            values = r.get("values", [])
            if values:
                last_value = values[-1][1] if len(values[-1]) > 1 else None
                if last_value is not None:
                    try:
                        value_float = float(last_value)
                        lines.append(f"  {label_str}: {value_float:.6f} (last of {len(values)} points)")
                    except (ValueError, TypeError):
                        lines.append(f"  {label_str}: {last_value}")
                else:
                    lines.append(f"  {label_str}: (no value)")
            else:
                lines.append(f"  {label_str}: (no values)")
        else:
            # Instant query: show single value
            value = r.get("value", [None, None])[1] if r.get("value") else None
            if value is not None:
                try:
                    value_float = float(value)
                    lines.append(f"  {label_str}: {value_float:.6f}")
                except (ValueError, TypeError):
                    lines.append(f"  {label_str}: {value}")
            else:
                lines.append(f"  {label_str}: (no value)")
    
    return "\n".join(lines) if lines else "(No data)"


def format_es_result(result):
    """Format Elasticsearch ES|QL result in compact form"""
    if not result:
        return "(Error or no data)"
    
    columns = result.get("columns", [])
    values = result.get("values", [])
    
    if not columns or not values:
        return "(No results)"
    
    lines = []
    for row in values:
        parts = []
        for i, col in enumerate(columns):
            val = row[i] if i < len(row) else None
            if val is not None:
                # Handle both dict and object column formats
                col_name = col.get("name") if isinstance(col, dict) else getattr(col, "name", f"col_{i}")
                if isinstance(val, (int, float)):
                    parts.append(f"{col_name}={val:.6f}" if isinstance(val, float) else f"{col_name}={val}")
                else:
                    parts.append(f"{col_name}={val}")
        if parts:
            lines.append(f"  {{{', '.join(parts)}}}")
    
    return "\n".join(lines) if lines else "(No data)"


def summarize(name, latencies):
    p50 = statistics.median(latencies)
    latencies_sorted = sorted(latencies)
    idx95 = int(0.95 * (len(latencies_sorted) - 1))
    p95 = latencies_sorted[idx95]
    print(f"{name}: p50={p50:.1f} ms, p95={p95:.1f} ms")


if __name__ == "__main__":
    # Check if we should print results (for verification)
    PRINT_RESULTS = os.getenv("PRINT_RESULTS", "false").lower() == "true"
    
    for q in QUERIES:
        if q.get("skip"):
            print(f"\n=== {q['name']} ===")
            print("Skipping (skip=true)")
            continue

        print(f"\n=== {q['name']} ===")
        prom_lats, prom_result = bench_prom(q)
        es_lats, es_result = bench_esql(q)

        if PRINT_RESULTS:
            print("\n[Prometheus Result]:")
            print(format_prom_result(prom_result))
            
            print("\n[Elasticsearch ES|QL Result]:")
            print(format_es_result(es_result))
            print()

        summarize("Prometheus", prom_lats)
        summarize("Elasticsearch ES|QL", es_lats)
