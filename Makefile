.PHONY: install test run-api run-ehr run-ui compose-up compose-down

install:
	python -m pip install -r requirements.txt

test:
	python -m pytest -q

run-api:
	uvicorn app.main:app --reload --port 8000

run-ehr:
	uvicorn mock_ehr:ehr_app --reload --port 8001

run-ui:
	API_BASE_URL=http://127.0.0.1:8000/api/v1 streamlit run frontend.py

compose-up:
	docker compose up --build

compose-down:
	docker compose down
