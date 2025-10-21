.PHONY: install test normalize geocode

PY?=python3

install:
	$(PY) -m pip install -r requirements.txt

test:
	pytest -q

# Example:
# make normalize IN=data/example_input.csv OUT=data/normalized.csv
normalize:
	$(PY) src/normalize_addresses.py --input "$(IN)" --output "$(OUT)" --config "config/config.yml"

# Example:
# make geocode IN=data/normalized.csv OUT=data/geocode.csv LOG=data/logs/geocode_api_log.jsonl
geocode:
	$(PY) src/geocode.py \
		--normalized "$(IN)" \
		--output "$(OUT)" \
		--config "config/config.yml" \
		--log "$(LOG)"
