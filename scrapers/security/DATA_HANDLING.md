# Manejo de datos sensibles

## Política

La DB/API/bot solo deben consumir datos saneados.  
Los dumps raw no deben estar en Git ni en salidas públicas.

## Tratamiento

| Tipo | Manejo |
|---|---|
| Cédulas / DNI / RUT | Redactar o HMAC si se requiere correlación |
| Teléfonos | Redactar o cifrar si existe finalidad operativa |
| Menores | Redactar siempre |
| Fallecidos / heridos / desaparecidos | Revisión humana y restricción |
| Direcciones exactas | Generalizar |
| Fotos / videos | No procesar automáticamente sin revisión |
| Logs | Nunca incluir PII ni dumps completos |

## Regla

Raw cifrado solo en cuarentena.  
HMAC para deduplicar identidad.  
Salida operacional solo saneada.
