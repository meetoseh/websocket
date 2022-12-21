#!/usr/bin/env bash
main() {
    . venv/bin/activate
    uvicorn main:app --host 0.0.0.0 --port 80
}

main
