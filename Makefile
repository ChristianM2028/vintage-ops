.PHONY: setup migrate run test
setup:
	python -m venv .venv && .venv/bin/pip install -r requirements.txt
migrate:
	.venv/bin/python -c 'from config import Config; from db import Database; d=Database(Config().database_path); d.migrate()'
run:
	.venv/bin/flask --app app run --debug
test:
	.venv/bin/pytest -q
