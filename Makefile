# Name of your compose binary (docker-compose vs docker compose)
COMPOSE = docker compose

# Default target
.PHONY: all
all: up

# ------------------------------------------------------------
# Start only the ingestion/query environment (not the bench)
# ------------------------------------------------------------
.PHONY: up
up:
	$(COMPOSE) down --remove-orphans
	$(COMPOSE) up --build -d elasticsearch prometheus metrics-gen
	@echo ""
	@echo "⬆️  Stack is starting. Wait ~30 seconds, then run: make bench"
	@echo ""

# ------------------------------------------------------------
# Run benchmark (Ephemeral container, no result printing)
# ------------------------------------------------------------
.PHONY: bench
bench:
	$(COMPOSE) run --rm bench

# ------------------------------------------------------------
# Run benchmark with result verification (prints results)
# ------------------------------------------------------------
.PHONY: bench_verify
bench_verify:
	$(COMPOSE) run --rm -e PRINT_RESULTS=true bench

# ------------------------------------------------------------
# Show stats in Prometheus and Elasticsearch
# ------------------------------------------------------------
.PHONY: stats
stats:
	@echo "========================================="
	@echo "Data Stats"
	@echo "========================================="
	@echo ""
	@echo "Configuration:"
	@echo "  Tick interval: 5s (TICK_SECONDS)"
	@echo "  Prometheus scrape: 5s"
	@echo "  Note: Assumes continuous ingestion (no gaps)"
	@echo ""
	@echo "Prometheus:"
	@echo "  Active series: $$(curl -s 'http://localhost:9090/api/v1/query?query=count(http_requests_qps)' | jq -r '.data.result[0].value[1]' 2>/dev/null || echo 'Error')"
	@echo ""
	@echo "Elasticsearch:"
	@echo "  Total documents: $$(curl -s 'http://localhost:9200/metrics-http/_count' | jq -r '.count' 2>/dev/null || echo 'Error')"
	@echo "  Time range (ES): $$(curl -s 'http://localhost:9200/_query?format=json' -H 'Content-Type: application/json' -d '{"query":"FROM metrics-http | STATS min_ts=MIN(@timestamp),max_ts=MAX(@timestamp)"}' 2>/dev/null | jq -r '.values[0] | if . then \"\\(.[0]) to \\(.[1])\" else \"Error\" end' 2>/dev/null || echo 'Error')"
	@echo ""
	@echo "Expected: 20,000 series (2000 instances × 5 status codes × 2 methods)"
	@echo ""
	@echo "Estimated time coverage:"
	@echo "  ES docs / series / tick = ticks of data"
	@echo "  $$(curl -s 'http://localhost:9200/metrics-http/_count' | jq -r '.count' 2>/dev/null || echo '0') / 20000 / 1 (per tick) = ~$$(echo \"$$(curl -s 'http://localhost:9200/metrics-http/_count' | jq -r '.count' 2>/dev/null || echo '0') / 20000\" | bc 2>/dev/null || echo '?') ticks"
	@echo "  At 5s/tick = ~$$(echo \"$$(curl -s 'http://localhost:9200/metrics-http/_count' | jq -r '.count' 2>/dev/null || echo '0') / 20000 * 5\" | bc 2>/dev/null || echo '?') seconds of data"
	@echo "========================================="

# ------------------------------------------------------------
# View logs (tailing)
# ------------------------------------------------------------
.PHONY: logs
logs:
	$(COMPOSE) logs -f

# ------------------------------------------------------------
# Start/Stop ingestion (metrics generator) only
# ------------------------------------------------------------
.PHONY: ingest-start
ingest-start:
	$(COMPOSE) up --build -d metrics-gen
	@echo ""
	@echo "🚀 metrics-gen ingestion started."
	@echo ""

.PHONY: ingest-stop
ingest-stop:
	$(COMPOSE) stop metrics-gen
	@echo ""
	@echo "🛑 metrics-gen ingestion stopped."
	@echo ""

# ------------------------------------------------------------
# Rebuild all images cleanly
# ------------------------------------------------------------
.PHONY: rebuild
rebuild:
	$(COMPOSE) down --remove-orphans
	$(COMPOSE) build --no-cache
	@echo ""
	@echo "🔨 Rebuild complete. Start stack with: make up"
	@echo ""

# ------------------------------------------------------------
# Rebuild only the bench service
# ------------------------------------------------------------
.PHONY: rebuild_bench
rebuild_bench:
	$(COMPOSE) build --no-cache bench
	@echo ""
	@echo "🔨 Bench service rebuilt. Run with: make bench"
	@echo ""

# ------------------------------------------------------------
# Stop everything
# ------------------------------------------------------------
.PHONY: down
down:
	$(COMPOSE) down --remove-orphans
	@echo ""
	@echo "🛑 Everything stopped."
	@echo ""

# ------------------------------------------------------------
# Wipe ES data completely (CAREFUL)
# ------------------------------------------------------------
.PHONY: clean
clean:
	$(COMPOSE) down --volumes --remove-orphans
	@echo ""
	@echo "🧹 All data volumes removed."
	@echo ""
