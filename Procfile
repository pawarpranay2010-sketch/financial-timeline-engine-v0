# Production web process — used by Render, Railway, Heroku and other
# Procfile-based PaaS platforms. Binds to the platform-injected PORT.
web: uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-5000}
