# SENTINEL Gemini / Free-Alternative Migration Patch

This patch replaces OpenAI-specific dependencies, API references, model names, and provider-locked wording with Gemini-first or free/open alternatives for the SENTINEL hackathon stack. The goal is to make the repo, planner, and strategy docs consistent, cheaper, and easier to defend during judging.

## Migration policy

Use this policy everywhere in the project:

- **Primary reasoning model:** Gemini Flash.
- **Free / local fallback model:** Phi-3-mini or Qwen2.5 Instruct.
- **Fine-tuning path:** Phi-3-mini on Kaggle via Unsloth.
- **Embeddings:** sentence-transformers (`all-MiniLM-L6-v2`) by default; Gemini embeddings optional.
- **Environment variable:** `GEMINI_API_KEY`, never `OPENAI_API_KEY`.
- **Judge-facing wording:** “model-agnostic architecture” and “Gemini-powered reasoning engine,” not OpenAI-specific phrasing.

---

## 1) Replace dependency/setup blocks

### Replace this old setup block

```bash
python -m venv sentinel-env
source sentinel-env/bin/activate  # or .\\sentinel-env\\Scripts\\activate on Windows
pip install openai langchain langgraph llama-index llama-index-llms-openai llama-index-embeddings-openai
pip install chromadb fastapi uvicorn pydantic numpy scipy httpx python-dotenv
pip install unsloth trl transformers datasets  # for fine-tuning prep
npm install -g create-vite
python -c "import openai; import langgraph; import llama_index; import chromadb; print('All imports OK')"
```

### With this new setup block

```bash
python -m venv sentinel-env
source sentinel-env/bin/activate  # or .\\sentinel-env\\Scripts\\activate on Windows
pip install google-genai langchain langgraph llama-index chromadb
pip install fastapi uvicorn pydantic numpy scipy httpx python-dotenv
pip install sentence-transformers unsloth trl transformers datasets
npm install -g create-vite
python -c "import langgraph, llama_index, chromadb; print('Core imports OK')"
```

### Notes

- `google-genai` is the preferred Gemini client package.
- `sentence-transformers` removes the need for OpenAI embeddings.
- If LlamaIndex provider-specific Gemini integration is not stable enough during the hackathon, keep retrieval simple: chunk PDF -> embed with sentence-transformers -> store in ChromaDB -> cosine similarity search -> pass top chunks to Gemini.

---

## 2) Replace environment variables

### Replace

```env
OPENAI_API_KEY=sk-xxx
```

### With

```env
GEMINI_API_KEY=your_gemini_key_here
```

### Replace in repo structure docs

```text
.env.example          ← GEMINI_API_KEY=your_gemini_key_here
```

---

## 3) Replace account-setup checklist text

### Replace

- OpenAI account → add $20 credits → save API key in `.env` file

### With

- Google AI Studio / Gemini API account → generate API key → save `GEMINI_API_KEY` in `.env`

### Keep unchanged

- Kaggle account → verify phone → enable GPU in notebook settings → test T4 access
- Railway.app account → connect GitHub → test deploy with a hello-world FastAPI

---

## 4) Replace API verification checklist text

### Replace

```text
OpenAI API key works: openai.ChatCompletion.create(model="gpt-4o-mini", ...) returns
```

### With

```text
Gemini API key works: a small Gemini Flash test request returns a valid response
```

### Optional code example for internal docs

```python
from google import genai
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
resp = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Reply with: OK"
)
print(resp.text)
```

---

## 5) Replace repo structure references

### Replace this repo snippet

```text
sentinel/
├── README.md
├── .gitignore
├── .env.example          ← OPENAI_API_KEY=sk-xxx
├── backend/
│   ├── main.py
│   ├── agent.py
│   ├── rag.py
│   ├── simulator.py
│   ├── evaluator.py
│   ├── requirements.txt
│   ├── Dockerfile
│   └── data/ecss/
├── frontend/
├── notebooks/
├── demo_cache/
├── evaluation/
└── docs/
```

### With this version

```text
sentinel/
├── README.md
├── .gitignore
├── .env.example          ← GEMINI_API_KEY=your_gemini_key_here
├── backend/
│   ├── main.py
│   ├── agent.py
│   ├── rag.py
│   ├── simulator.py
│   ├── evaluator.py
│   ├── requirements.txt
│   ├── Dockerfile
│   └── data/ecss/
├── frontend/
├── notebooks/
├── demo_cache/
├── evaluation/
└── docs/
```

---

## 6) Replace model references across all docs

### Global substitutions

Use these replacements everywhere in planner, strategy, README, skills, and pitch notes:

| Old text | New text |
|---|---|
| GPT-4o-mini | Gemini Flash |
| GPT-4o | Gemini 2.5 Pro or Gemini Pro-class model |
| OpenAI | Gemini API / Google AI Studio |
| OpenAIEmbedding | sentence-transformers embedding model or Gemini embeddings |
| llama-index-llms-openai | Gemini-compatible integration or custom retrieval pipeline |
| llama-index-embeddings-openai | sentence-transformers or Gemini embeddings |
| OpenAI API key | Gemini API key |
| `pip install openai` | `pip install google-genai` |

### Safer architecture wording

Replace any phrase like:

> Phi-3-mini (QLoRA fine-tuned) OR GPT-4o-mini

with:

> Phi-3-mini / Qwen2.5 (fine-tuned or local fallback) OR Gemini Flash for hosted reasoning

And replace:

> Works with any LLM. Show that swapping GPT-4o-mini for Phi-3-mini works.

with:

> Works with any LLM. Show that swapping Gemini Flash for Phi-3-mini or Qwen2.5 works.

---

## 7) Replace tech-stack tables

### Replace this old row set

| Layer | Technology | Why | Install |
|---|---|---|---|
| LLM (demo) | GPT-4o-mini API | Fast, cheap, reliable for hackathon | `pip install openai` |
| LLM (fine-tuned) | Phi-3-mini via Unsloth | Free on Kaggle T4 | `pip install unsloth` |
| RAG framework | LlamaIndex | Handles PDF natively | `pip install llama-index` |

### With this new row set

| Layer | Technology | Why | Install |
|---|---|---|---|
| LLM (demo) | Gemini Flash | Fast, strong reasoning, easy hosted inference path | `pip install google-genai` |
| LLM (free/local fallback) | Phi-3-mini or Qwen2.5 Instruct | Free-friendly, Kaggle/local experimentation | `pip install transformers` |
| LLM (fine-tuned) | Phi-3-mini via Unsloth | Best fit for Kaggle QLoRA workflow | `pip install unsloth` |
| RAG framework | LlamaIndex or custom retrieval | Use whichever is more stable during hackathon | `pip install llama-index` |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` | Free, local, simple | `pip install sentence-transformers` |

### Recommended short version for judge-facing docs

- Gemini Flash for live reasoning
- Phi-3-mini / Qwen2.5 for free fallback and experimentation
- sentence-transformers + ChromaDB for RAG retrieval
- LangGraph for orchestration
- FastAPI + React for demo system

---

## 8) Replace RAG implementation wording

### Replace this wording

> LlamaIndex SimpleDirectoryReader → SentenceSplitter → OpenAIEmbedding → ChromaDB

### With this wording

> PDF loader → chunking → sentence-transformers embeddings → ChromaDB → top-k retrieval into Gemini prompt

### Or this if keeping LlamaIndex

> LlamaIndex loads ECSS PDFs, chunks them, stores embeddings in ChromaDB using a free/local embedding model, and returns top-k procedures to the Gemini reasoning layer.

---

## 9) Replace risk-register items

### Replace these old risks

- OpenAI API key exhausted
- use GPT-4o-mini specifically
- switch to Phi-3-mini on Kaggle for inference

### With these new risks

- Gemini API quota exhausted or unavailable
- primary hosted reasoning too slow during live demo
- fallback to Phi-3-mini / Qwen2.5 or cached demo outputs

### New risk-register row text

| Risk | Probability | Impact | Mitigation | Fallback |
|---|---|---|---|---|
| Gemini API quota/rate limit during demo | Medium | High | Pre-cache 3 demo scenarios, reduce token length, keep prompts tight | Serve cached responses or run local/open-model fallback |
| Hosted model latency too slow | Medium | Medium | Use Gemini Flash, reduce retrieved chunks from 5 to 3 | Cache first 80% of reasoning, stream saved events |
| Hosted provider unavailable | Low-Medium | High | Keep local/open model path ready | Phi-3-mini / Qwen2.5 or screenshot-backed demo |

---

## 10) Replace evaluation and cost wording

### Replace

> At GPT-4o-mini pricing this costs approximately $1–3.

### With

> Evaluation should be designed to stay within free-tier or low-cost limits; if hosted quota becomes unreliable, run the same harness on Phi-3-mini or Qwen2.5 for comparison.

### Replace

> Base GPT-4o-mini without our system scores XX on our test set.

### With

> Base Gemini Flash or a plain baseline LLM prompt without our full system scores XX on our test set.

### Better judge-safe wording

> The LLM is one component. The measurable gain comes from structured crash-dump parsing, anomaly pre-filtering, RAG grounding, safety validation, and auditable reasoning visualization.

---

## 11) Replace judge-Q&A language

### Replace

> This is just calling GPT-4 with a prompt.

### With

> This is just calling an LLM API with a prompt.

### Replace answer with this stronger provider-agnostic version

> The novelty is not the choice of provider. The novelty is the system architecture: crash-dump parsing, anomaly pre-filtering, retrieval over ECSS procedures, structured multi-hypothesis diagnosis, safety validation, and auditable causal DAG output. The hosted LLM is one component inside that pipeline.

### Replace

> Base GPT-4o-mini without our system scores XX.

### With

> A plain baseline LLM prompt without our full system scores XX.

This prevents the pitch from sounding dependent on one vendor.

---

## 12) Replace architecture text

### Replace this sentence

> Phi-3-mini (QLoRA fine-tuned) OR GPT-4o-mini

### With

> Gemini Flash for hosted reasoning, with Phi-3-mini or Qwen2.5 as free/local fallback and experimentation path

### Replace this sentence

> use GPT-4o-mini only for live demo inference where latency matters

### With

> use Gemini Flash for live demo inference where latency and response quality matter

---

## 13) Replace day-plan task wording

### Replace Day 3 evaluation lines like

> Compare accuracy/speed to base GPT-4o-mini.

### With

> Compare accuracy/speed to the Gemini Flash baseline and, if available, to a smaller open-model baseline.

### Replace prompt-hardening line like

> Base GPT-4o-mini without these components scores XX on our test set.

### With

> A plain hosted-model baseline without these components scores XX on our test set.

### Replace speed-optimization line like

> use GPT-4o-mini specifically

### With

> use Gemini Flash specifically for the hosted demo path

---

## 14) Replace README / pitch wording

### Old wording

- Powered by GPT-4o-mini
- OpenAI-based satellite recovery system
- OpenAI API-driven diagnosis engine

### New wording

- Powered by Gemini Flash with model-agnostic fallback support
- Gemini-first satellite recovery reasoning system
- Model-agnostic diagnosis engine with Gemini-hosted reasoning and free/local fallback options

### Best one-line project description

> SENTINEL is a Gemini-first, model-agnostic spacecraft safe-mode diagnosis and recovery system that combines crash-dump parsing, anomaly pre-filtering, RAG-grounded reasoning, safety validation, and auditable causal-chain visualization.

---

## 15) Direct search-and-replace checklist

Run this checklist across every `.md`, `.env.example`, README, and setup doc.

### Search for and replace

- `OpenAI` -> `Gemini API` or `Google AI Studio` depending on context
- `openai` -> `google-genai` or generic `hosted LLM client`
- `OPENAI_API_KEY` -> `GEMINI_API_KEY`
- `gpt-4o-mini` -> `Gemini Flash`
- `GPT-4o-mini` -> `Gemini Flash`
- `GPT-4o` -> `Gemini Pro-class model` or `Gemini 2.5 Pro` depending on context
- `llama-index-llms-openai` -> remove or replace with Gemini-compatible integration text
- `llama-index-embeddings-openai` -> `sentence-transformers`
- `OpenAIEmbedding` -> `sentence-transformers embedding model`
- `openai.ChatCompletion.create` -> `Gemini client generate_content call`

### Search for and rewrite manually

- Any “API cost” paragraph tied to GPT pricing
- Any “judge defense” sentence using GPT-specific branding
- Any “fallback” sentence that assumes OpenAI is primary
- Any `.env.example` sample values

---

## 16) Final canonical text block to paste into docs

Use this canonical block anywhere you need a clean model-policy paragraph:

> SENTINEL uses a **Gemini-first, model-agnostic architecture**. Gemini Flash is the primary hosted reasoning model for live demo inference, while Phi-3-mini or Qwen2.5 provide free/local fallback and experimentation paths. Retrieval is grounded in ECSS engineering documents using ChromaDB with free/local embeddings, and every model output passes through structured validation, safety checks, and auditable causal-chain rendering before presentation.

---

## 17) Optional code-policy paragraph for the team

> Do not write new code or docs that mention OpenAI, GPT-4o, `OPENAI_API_KEY`, or OpenAI-specific embedding packages. All new implementation, setup instructions, and pitch content must follow the Gemini-first / free-fallback policy to keep the project consistent, cheaper, and easier to defend.

---

## 18) Short “before vs after” summary

### Before

- OpenAI-specific package installs
- OpenAI API key in repo docs
- GPT-4o-mini in architecture, evaluation, and pitch text
- Provider-locked RAG and embedding wording

### After

- Gemini-first hosted path
- Phi-3-mini / Qwen2.5 free fallback path
- sentence-transformers-based embeddings
- provider-agnostic, judge-safe project language

