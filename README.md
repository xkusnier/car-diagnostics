# car-diagnostics backend

## Lokálne spustenie

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# alebo: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Server beží na:

- `http://localhost:5000/`
- health check: `http://localhost:5000/api/health`
- Swagger UI: `http://localhost:5000/apidocs/`
- aliasy na Swagger: `/swagger`, `/docs`, `/api/docs`

## Render

Používa sa Python 3.11.9 podľa `runtime.txt`. Štartovací príkaz je v `render.yaml`:

```bash
gunicorn -k eventlet -w 1 app:app
```

Ak chceš vypnúť automatické vytváranie tabuliek pri štarte, nastav env premennú:

```bash
AUTO_CREATE_TABLES=false
```
