"""Jobs de consolidacion (Stage 2) para el pipeline de VZLA.

Este paquete alberga el job de auto-merge de Event/AcopioCenter por dedup_hash
(#91, faceta del EPIC #82) y sus contratos de acceso a datos.

El acceso a datos se hace SIEMPRE detras de un puerto (`ConsolidationDataPort`):
la decision de arquitectura del backend (PostgREST directo vs Vercel) sigue
pendiente en el equipo, asi que aqui no vive ningun adapter concreto de
produccion, solo la interfaz y un `FakeInMemoryAdapter` para tests offline.
"""
