import os

# NOTA DE ARQUITECTURA STATELESS: En entornos con auto-escalado horizontal (Docker/Kubernetes),
# SQLite causa fragmentación de estado efímero. Para producción, DEBE reemplazarse estrictamente
# por una URI de PostgreSQL externa (RDS/Supabase) mediante la variable de entorno DATABASE_URL.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./vzla_dedup.db")

API_SECRET_KEY = os.getenv("API_SECRET_KEY", "dev-api-secret-key-change-in-prod")
PII_HMAC_SECRET = os.getenv("PII_HMAC_SECRET", "dev-pii-hmac-secret-change-in-prod")
