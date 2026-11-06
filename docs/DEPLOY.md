# DEPLOY — javni demo (Hugging Face Spaces, Docker)

Besplatno (CPU Basic: 2 vCPU, 16 GB RAM). Space build-uje isti `Dockerfile` kao lokalno (sa
Tesseract OCR-om i ugrađenim modelom za pretragu). Odgovore daju besplatni nivoi **Groq**
(glavni) i **Gemini** (rezerva) — ADR-021/022; Claude se uključuje samo dodavanjem ključa.

## 1. Provera lokalno

```bash
uv run uvicorn docassist.api:app --reload      # → http://localhost:8000
docker build -t doc-assistant . && docker run -p 8000:8000 --env-file .env doc-assistant
```

## 2. Jednokratno podešavanje

1. Nalog na <https://huggingface.co> → **New Space** → ime `doc-assistant`, SDK **Docker**
   (*Blank*), hardware **CPU basic (free)**, vidljivost *Public*.
2. Space → **Settings → Variables and secrets**:

   | Tip | Ime | Vrednost | Zašto |
   |---|---|---|---|
   | Secret | `GROQ_API_KEY` | `gsk_…` | glavni model (besplatno) |
   | Secret | `GEMINI_API_KEY` | `AIza…` | rezerva kad Groq potroši limit (opciono) |
   | Secret | `API_TOKEN` | nasumičan string | štiti API od skripti; browser radi preko sesije |
   | Variable | `LLM_PROVIDER` | `groq` | |
   | Variable | `PORT` | `7860` | port koji Space očekuje |
   | Variable | `ALLOW_UPLOADS` | `false` | fiksni izmišljeni demo dokumenti: bez upload-a i brisanja |
   | Variable | `MAX_QUESTIONS_PER_SESSION` | `15` | čuva besplatne limite modela |
   | Variable | `MAX_QUESTIONS_PER_IP_HOUR` | `40` | |
   | Variable | `EMBED_ORIGINS` | `https://huggingface.co` | Space prikazuje aplikaciju u iframe-u na huggingface.co |

   Bez `ESCALATION_WEBHOOK_URL` (n8n nije online) prosleđena pitanja se upisuju u lokalni fajl
   Space-a — dugme radi i daje referencu.
3. <https://huggingface.co/settings/tokens> → novi token, tip **Write**.
4. GitHub repo → **Settings → Secrets and variables → Actions**:
   - *Secret* `HF_TOKEN` = taj token
   - *Variable* `HF_SPACE` = `<tvoj-hf-username>/doc-assistant`
5. GitHub → **Actions → Deploy demo → Run workflow** (posle toga svaki merge u `main` sam
   objavljuje novu verziju).

Workflow (`.github/workflows/deploy-space.yml`) objavljuje `main` u Space: README zamenjuje sa
`deploy/space_readme.md` (Space podešavanja: `sdk: docker`, `app_port: 7860`), a PDF/DOCX/slike
šalje preko Git LFS-a (Hub drugačije odbija binarne fajlove). Space sam build-uje image
(~10 min prvi put). Pri prvom pitanju aplikacija indeksira demo dokumente.

## 3. Posle deploy-a

- Proveri: otvori Space, postavi pitanje iz primera, klikni izvor (pregled sa označenim pasusom),
  prebaci na *Agent*.
- Link (`https://<user>-doc-assistant.hf.space`) upiši u `README.md` (zadatak 4.4).
- Disk Space-a nije trajan: posle restarta indeks, log i razgovori kreću ispočetka (za demo u redu).
- Ključevi besplatnih nivoa troše samo limite (Groq: 1.000 zahteva dnevno po modelu), ne novac.
  **U demo nikad ne stavljaj prave firmine dokumente** — besplatni nivoi smeju da koriste sadržaj.

---

# Sopstveni server (VPS) — za pravo korišćenje u firmi (ADR-040)

Sve u jednom: aplikacija, **HTTPS** i **lozinka** (Caddy), **n8n** i **noćna rezervna kopija**.
Fajlovi: `deploy/server/` (`compose.yaml`, `Caddyfile`, `.env.example`). Spolja se vide samo portovi
80/443 (Caddy); aplikacija i n8n nemaju javni port.

## Koliko košta (novembar 2026)

| Stavka | Cena |
|---|---|
| Server, npr. Hetzner CX23 (2 vCPU, 4 GB) / CX33 (4 GB+, za n8n udobnije) | ~4,5 € / ~8,5 € mesečno |
| AI model (plaćeni nivo; besplatni smeju da koriste dokumente) | ~0,50 $ (Groq gpt-oss-120b), ~0,35 $ (Claude Haiku 5.5), ~7 $ (Sonnet 5.5) na 1.000 pitanja |
| Domen | ~10 € godišnje (ili poddomen firme) |
| HTTPS sertifikat, rezervne kopije | 0 € |

Cene proveri na sajtu pre plaćanja (Hetzner ih je 2026. menjao dvaput).

## 1. Server i domen

1. Iznajmi server sa **Ubuntu 24.04** (npr. Hetzner Cloud → *Add Server*, dodaj svoj SSH ključ).
2. U DNS-u domena dodaj **A zapise** na IP adresu servera: `asistent.firma.rs` i (za n8n)
   `n8n.firma.rs`.
3. Na serveru (preko `ssh root@<ip>`):

   ```bash
   curl -fsSL https://get.docker.com | sh                       # Docker + Compose
   ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
   git clone https://github.com/<ti>/doc-assistant.git && cd doc-assistant
   cp deploy/server/.env.example deploy/server/.env
   ```

## 2. Podešavanja (`deploy/server/.env`)

- `DOMAIN`, `N8N_DOMAIN`, `ACME_EMAIL` — adrese iz koraka 1 i mejl za obaveštenja o sertifikatu.
- Lozinka za ulaz (jedna za firmu; browser je pita jednom i pamti):

  ```bash
  docker run --rm caddy:2.10-alpine caddy hash-password --plaintext 'izaberi-lozinku'
  ```

  rezultat upiši kao `AUTH_PASSWORD_HASH='…'` — **sa jednostrukim navodnicima** (u hešu je `$`).
- `GROQ_API_KEY` (ili `ANTHROPIC_API_KEY` uz `LLM_PROVIDER=claude`) — **plaćeni nivo**.
- `API_TOKEN` — `openssl rand -hex 32`.
- `ESCALATION_TEAMS` — pravi timovi i njihovi mejlovi.

## 3. Pokretanje

```bash
docker compose -f deploy/server/compose.yaml up -d --build      # prvi put ~10 min (slika)
docker compose -f deploy/server/compose.yaml ps                 # sve "running"/"healthy"
```

Otvori `https://asistent.firma.rs` → browser traži korisnika i lozinku → aplikacija. Sertifikat
Caddy pribavlja sam pri prvom otvaranju (domen mora već da pokazuje na server).

## 4. n8n (eskalacije i nedeljni izveštaj)

1. Otvori `https://n8n.firma.rs` i napravi vlasnički nalog (prvi posetilac ga pravi — uradi odmah).
2. Uvezi tokove:

   ```bash
   C="docker compose -f deploy/server/compose.yaml exec -T n8n n8n"
   $C import:credentials --input=/automations/n8n_credentials_mailpit.json
   $C import:workflow --input=/automations/escalation_workflow.json
   $C import:workflow --input=/automations/gap_report_workflow.json
   $C update:workflow --id=DocAssistEscalat --active=true
   $C update:workflow --id=DocAssistGapRepo --active=true
   docker compose -f deploy/server/compose.yaml restart n8n
   ```

3. U n8n-u: *Credentials → Mailpit (local test inbox)* → upiši **pravi SMTP** (firmin mejl server ili
   servis za slanje mejlova) umesto `mailpit:1025`.

Webhook-ovi n8n-a su zatvoreni za internet (zove ih samo aplikacija, iznutra).

## 5. Rezervne kopije

Svake noći u 3:00 kontejner `backup` pakuje indeks, dokumente, log, razgovore, ulazni folder i n8n u
`deploy/server/backups/doc-assistant-GGGG-MM-DD.tar.gz` (čuva se 14 dana; aplikacija se za to
pauzira par sekundi). **Kopiraj ih i van servera** (npr. `rsync` na drugi računar ili Hetzner
*Backups* opcija za ~20 % cene servera).

```bash
docker compose -f deploy/server/compose.yaml exec backup backup        # kopija odmah
```

Vraćanje: zaustavi (`… down`), raspakuj arhivu u volume (`docker run --rm -v
doc-assistant-server_state:/state -v $PWD/deploy/server/backups:/b alpine tar -xzf
/b/<arhiva> -C / --strip-components=1 backup/state`), pa `… up -d`. Isto za ulazni folder i n8n:
volume `doc-assistant-server_inbox` sa `backup/inbox` (u `/`), odnosno `doc-assistant-server_n8n`
montiran na `/n8n` sa `backup/n8n`. Provereno: obrisani podaci aplikacije su se ovako vratili.

## 6. Ažuriranje

Ručno: `git pull && docker compose -f deploy/server/compose.yaml up -d --build`.
Automatski posle svakog spajanja u `main`: GitHub → *Settings → Secrets and variables → Actions*:

| Tip | Ime | Vrednost |
|---|---|---|
| Variable | `SERVER_HOST` | `deploy@asistent.firma.rs` (korisnik na serveru koji sme `docker`) |
| Variable | `SERVER_DIR` | folder klona na serveru (podrazumevano `doc-assistant`) |
| Secret | `SERVER_SSH_KEY` | privatni ključ (`ssh-keygen -t ed25519`; javni ide u `~/.ssh/authorized_keys` tog korisnika) |
| Secret | `SERVER_KNOWN_HOSTS` | izlaz `ssh-keyscan asistent.firma.rs` |

Workflow `.github/workflows/deploy-server.yml` se bez `SERVER_HOST` sam preskače.

## Šta na serveru radi drugačije nego lokalno

- **Automatski uvoz iz foldera**: "Otvori folder" i Windows prozor za izbor rade samo na računaru na
  kom je server, pa ih na VPS-u nema; folder je `/state/inbox` u kontejneru. Fajlovi tamo stižu
  preko n8n-a (volume `inbox` je u n8n-u na `/inbox` — npr. tok *Email Trigger (IMAP)* → *Write
  Binary File* u `/inbox` za mejl `dokumenti@firma.rs`). Otpremanje kroz aplikaciju radi normalno.
- **Jedna lozinka za sve.** Za lične naloge bez izmene koda: Cloudflare Access ispred domena
  (prijava firminim mejlom; besplatno za mali tim) — tada se `basic_auth` blok iz `Caddyfile`-a
  može ukloniti.
