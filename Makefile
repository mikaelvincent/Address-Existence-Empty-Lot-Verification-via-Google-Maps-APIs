.PHONY: install test normalize

PY?=python3

install:
	$(PY) -m pip install -r requirements.txt

test:
	pytest -q

# Example:
# make normalize IN=data/example_input.csv OUT=data/normalized.csv
normalize:
	$(PY) src/normalize_addresses.py --input "$(IN)" --output "$(OUT)" --config "config/config.yml"
