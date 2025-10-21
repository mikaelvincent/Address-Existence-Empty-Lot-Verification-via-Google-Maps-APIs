# Makefile
.PHONY: install test normalize geocode svmeta footprints validate

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

# Example:
# make svmeta IN=data/geocode.csv OUT=data/streetview_meta.csv LOG=data/logs/streetview_meta_api_log.jsonl
svmeta:
	$(PY) src/streetview_meta.py \
		--geocode "$(IN)" \
		--output "$(OUT)" \
		--config "config/config.yml" \
		--log "$(LOG)"

# Example:
# make footprints IN=data/geocode.csv FP="data/footprints/*.geojson" OUT=data/footprints.csv LOG=data/logs/footprints_log.jsonl
footprints:
	$(PY) src/footprints.py \
		--geocode "$(IN)" \
		--footprints $(FP) \
		--output "$(OUT)" \
		--config "config/config.yml" \
		--log "$(LOG)"

# Example:
# make validate GEOCODE=data/geocode.csv SVMETA=data/streetview_meta.csv FP=data/footprints.csv NORM=data/normalized.csv OUT=data/validation.csv LOG=data/logs/address_validation_api_log.jsonl
validate:
	$(PY) src/validate_postal.py \
		--geocode "$(GEOCODE)" \
		--svmeta "$(SVMETA)" \
		--footprints "$(FP)" \
		--normalized "$(NORM)" \
		--output "$(OUT)" \
		--config "config/config.yml" \
		--log "$(LOG)"
