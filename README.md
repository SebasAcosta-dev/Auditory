# Asistente de Facturacion Medica por Terminal

Este proyecto crea un asistente CLI para:

- consultar documentos de facturacion medica almacenados en Azure AI Search,
- o probar primero con una carpeta local de PDFs,
- guardar reportes en Azure Blob Storage,
- preguntar por estados recientes de cuentas,
- resumir riesgos de rechazo,
- generar informes en Markdown desde terminal.

## Arquitectura

1. El usuario escribe una pregunta en terminal.
2. El asistente busca documentos relevantes en Azure AI Search o en una carpeta local.
3. Si hay OpenAI o Azure OpenAI configurado, el contexto recuperado se envia al modelo.
4. Los informes pueden subirse automaticamente a Azure Blob Storage.
5. Si no hay modelo configurado, el sistema genera un analisis local basico.
6. La respuesta devuelve:
   - analisis,
   - alertas de rechazo,
   - acciones sugeridas,
   - y, si se solicita, un informe guardado en disco.

## Requisitos

- Python 3.10+
- Para el modo local: una carpeta con PDFs
- Para usar modelo: credenciales de OpenAI o Azure OpenAI
- Para el modo Azure documental: un indice en Azure AI Search

## Instalacion

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

## Modo local de prueba

Si aun no tienes Azure, usa:

```env
APP_MODE=local
LOCAL_DOCS_PATH=DEVOLUCIONES ACTA 20-11-25-20260401T072131Z-1-001
```

En este modo, el asistente:

- indexa PDFs de la carpeta,
- intenta leer texto con `pypdf`,
- y si no hay modelo configurado, responde con un analisis local basado en coincidencias.

## Configuracion de modelo con OpenAI

Si quieres conectar OpenAI directamente:

```env
MODEL_PROVIDER=openai
OPENAI_API_KEY=tu_api_key
OPENAI_MODEL=gpt-5.2
OPENAI_BASE_URL=https://api.openai.com/v1
```

## Configuracion de modelo con Azure OpenAI

Si quieres conectar Azure OpenAI:

```env
MODEL_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://tu-recurso-openai.openai.azure.com
AZURE_OPENAI_API_KEY=tu_clave_openai
AZURE_OPENAI_DEPLOYMENT=gpt-52-medical-billing
AZURE_OPENAI_API_VERSION=2024-10-21
```

## Configuracion del indice Azure

El indice de Azure AI Search deberia exponer, idealmente, estos campos:

- `content`: texto principal del documento
- `title`: nombre del documento o expediente
- `updated_at`: fecha del estado o ultima actualizacion
- `status`: estado de la cuenta
- `patient_name`: nombre del paciente
- `account_id`: identificador de cuenta

Puedes cambiar los nombres reales de tus campos en el archivo `.env`.

## Configuracion de Azure Storage para reportes

Si quieres subir los reportes generados a Azure Blob Storage, completa una de estas opciones:

```env
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net
AZURE_STORAGE_REPORTS_CONTAINER=reports
```

o bien:

```env
AZURE_STORAGE_ACCOUNT_URL=https://tu-storage.blob.core.windows.net
AZURE_STORAGE_ACCOUNT_KEY=tu_clave_storage
AZURE_STORAGE_REPORTS_CONTAINER=reports
```

## Uso

Modo interactivo:

```bash
python -m app.main
```

Comandos dentro del asistente:

- `pregunta <texto>`: busca por factura o tema y valida los soportes relacionados
- o simplemente escribe la pregunta directamente, sin anteponer `pregunta`
- `recientes`: lista cuentas recientes desde los documentos mas nuevos
- `dashboard`: genera un resumen tipo tablero con facturas en riesgo
- `informe [tema]`: genera un informe Markdown en la carpeta `reports/`; si no pones tema, crea un reporte general
- si Azure Storage esta configurado, el informe tambien se sube al contenedor configurado
- `salir`: termina la sesion

Cuando `MODEL_PROVIDER=none`, el asistente responde con logica local.
Cuando `MODEL_PROVIDER=openai` o `MODEL_PROVIDER=azure`, usa el modelo configurado para responder preguntas abiertas.

## Ejemplos

```text
pregunta Que cuentas tienen mayor riesgo de rechazo esta semana?
pregunta Resume los errores frecuentes en autorizaciones pendientes
informe Estado reciente de cuentas con observaciones y acciones recomendadas
recientes
```

## Ejemplo de prueba local

```text
pregunta Que cuentas aparecen como devueltas?
pregunta Busca la cuenta FE51165
dashboard
informe Resumen de devoluciones encontradas en la carpeta local
```

## Proximo paso recomendado

Si quieres, el siguiente paso natural es conectar este CLI con:

- autenticacion corporativa,
- filtros por fecha o aseguradora,
- exportacion a Excel o PDF,
- y una capa RAG mas avanzada con metadatos clinicos y administrativos.
