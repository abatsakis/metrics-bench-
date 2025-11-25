# Metrics Benchmark: Prometheus vs Elasticsearch

A performance benchmark comparing Prometheus PromQL and Elasticsearch ES|QL for time series metrics queries.

## Overview

This project benchmarks query performance between:
- **Prometheus** with PromQL
- **Elasticsearch** with ES|QL (Time Series Data Stream / TSDS)

It generates synthetic HTTP request metrics with configurable cardinality and runs equivalent queries against both systems to compare latency.

## Architecture

```
┌─────────────────┐
│  metrics-gen    │  Generates synthetic metrics
│                 │  - Exposes /metrics for Prometheus scraping
│                 │  - Pushes to Elasticsearch via _bulk API
└─────────────────┘
        │
        ├──> Prometheus (scrapes every 5s)
        │
        └──> Elasticsearch (TSDS with trial license)

┌─────────────────┐
│     bench       │  Runs queries against both systems
│                 │  - Measures p50/p95 latencies
│                 │  - Compares results
└─────────────────┘
```

## Quick Start

### Prerequisites
- Docker with Docker Compose
- 4GB+ RAM available for Elasticsearch
- `jq` (optional, for `make stats`)

### Start the Stack

```bash
# Clean start (recommended)
make clean
make up

# Wait ~30 seconds for initial data ingestion
```

### Verify Data

```bash
make stats
```

Expected output:
- 20,000 active series in Prometheus
- Documents accumulating in Elasticsearch (20,000 per tick)

### Run Benchmark

```bash
# Run benchmark (latency stats only)
make bench

# Run benchmark with result verification
make bench_verify
```

## Configuration

### Cardinality

Edit `docker-compose.yml` to adjust cardinality:

```yaml
metrics-gen:
  environment:
    - NUM_INSTANCES=2000      # Number of unique instances
    - NUM_STATUS_CODES=5      # Number of status codes (200, 500, 400, 404, 502)
    - NUM_METHODS=2           # Number of HTTP methods (GET, POST)
    - TICK_SECONDS=5          # Seconds between metric generations
```

**Total series** = `NUM_INSTANCES × NUM_STATUS_CODES × NUM_METHODS`

Default: 2000 × 5 × 2 = **20,000 series**

### Queries

Queries are defined in `bench/query_bench.py`. Current queries:

| Query | Description | Time Range | Step |
|-------|-------------|------------|------|
| Q1 | Average for single instance | 15m | instant |
| Q2 | Average for 100 instances (filtered) | 1h | 5m |
| Q3 | Average all series with sorting | 1h | 5m |
| Q4 | Average grouped by status_code | 4h | 5m |

Each query runs 5 times (configurable via `NUM_RUNS`) with 0.5s between runs.

Set `"skip": True` on any query to skip execution.

## Make Targets

| Target | Description |
|--------|-------------|
| `make up` | Start Elasticsearch, Prometheus, and metrics-gen |
| `make down` | Stop all services |
| `make clean` | Stop services and wipe all data volumes |
| `make bench` | Run benchmark (latency stats only) |
| `make bench_verify` | Run benchmark with result printing |
| `make stats` | Show data counts and time coverage |
| `make logs` | Tail logs from all services |
| `make rebuild` | Rebuild all images from scratch |
| `make rebuild_bench` | Rebuild only the bench service |
| `make ingest-start` | Start only the metrics-gen container |
| `make ingest-stop` | Stop metrics-gen (pause ingestion) |

## Data Storage

### Prometheus
- In-memory + local TSDB
- Default retention: 15 days
- Scrapes `/metrics` from metrics-gen every 5s

### Elasticsearch
- Time Series Data Stream (TSDS) with trial license
- Index pattern: `metrics-http-*`
- Document structure:
  ```json
  {
    "@timestamp": "2024-01-15T10:30:00.123Z",
    "http_requests_qps": 95.5,
    "job": "demo",
    "instance": "inst-00000",
    "status_code": "200",
    "method": "GET"
  }
  ```

## Performance Tips

### For faster queries:
- Reduce `NUM_INSTANCES` in docker-compose.yml
- Use shorter time ranges in queries
- Reduce query `step` interval

### For more realistic load:
- Increase `NUM_INSTANCES` to 10,000+
- Add more dimensions (e.g., `NUM_STATUS_CODES`)
- Let data accumulate for several hours before benchmarking

## Troubleshooting

### "Connection refused" errors
Services may still be starting. Wait 30+ seconds after `make up`.

### Timeout errors
Long-running queries (especially over 4+ hours) may timeout. Either:
- Reduce query time ranges
- Wait for less data to accumulate (run `make clean` then `make up`)
- Increase timeout in `bench/query_bench.py`

### "Unknown index [metrics-http]"
The TSDS data stream may not be ready. Check logs:
```bash
docker compose logs metrics-gen
```

Ensure you see: `[metrics-gen] TSDS template and data stream ready`

### Prometheus shows `(no value)`
For range queries, ensure `range_duration` and `step` are set in the query definition.

## Example Output

```
=== Q2_avg_filter_100_hosts_step ===

[Prometheus Result]:
  {exported_instance=inst-00100, ...}: 95.234567 (last of 12 points)
  {exported_instance=inst-00101, ...}: 98.123456 (last of 12 points)
  ...

[Elasticsearch ES|QL Result]:
  {avg_qps=95.234123, bucket=2024-01-15T10:00:00.000Z, instance=inst-00100, ...}
  {avg_qps=98.122987, bucket=2024-01-15T10:00:00.000Z, instance=inst-00101, ...}
  ...

Prometheus: p50=45.2 ms, p95=58.3 ms
Elasticsearch ES|QL: p50=125.7 ms, p95=187.4 ms
```

## License

MIT

