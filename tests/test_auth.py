"""User accounts and roles: employee, editor, admin (ADR-048)."""

import io
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docassist import cli, users
from docassist.api import SESSION_COOKIE, create_app
from docassist.api.routes import auth
from docassist.config import Settings
from docassist.llm import FakeProvider
from docassist.retrieval import FakeEmbedder, VectorStore
from docassist.users import UserError, UserStore, hash_password, verify_password

SAMPLE_DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs"
PASSWORD = "tajna-lozinka"


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    """The real scrypt cost (~65 ms a password) would only slow the tests down."""
    monkeypatch.setattr(users, "_SCRYPT_N", 2**4)


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    return replace(Settings.from_env(load_dotenv_file=False), login_required=True)


@pytest.fixture
def store(settings):
    """The users: one of each role, all with the same password."""
    store = UserStore(settings.log_db)
    for name in users.ROLES:
        store.add(name, PASSWORD, name)
    return store


@pytest.fixture
def app(settings, store, tmp_path):
    index = VectorStore(tmp_path / "index", FakeEmbedder())
    return create_app(settings=settings, store=index, llm_factory=lambda s, p: FakeProvider())


def sign_in(app, username, password=PASSWORD):
    client = TestClient(app)
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def upload_faq(app):
    files = [("files", ("faq.txt", (SAMPLE_DOCS / "faq.txt").read_bytes()))]
    assert sign_in(app, "editor").post("/documents", files=files).status_code == 200


# --- passwords and the user store --------------------------------------------------------


def test_a_password_is_stored_only_as_a_salted_hash():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)

    assert PASSWORD not in first and first != second  # a new salt every time
    assert verify_password(PASSWORD, first) and verify_password(PASSWORD, second)
    assert not verify_password("pogresna-lozinka", first)
    assert not verify_password(PASSWORD, "not a hash")


def test_user_names_roles_and_passwords_are_checked(settings):
    store = UserStore(settings.log_db)
    ana = store.add("  Ana.Petrovic@Firma.rs ", PASSWORD, "editor")

    assert (ana.username, ana.role) == ("ana.petrovic@firma.rs", "editor")
    assert store.verify("ANA.PETROVIC@firma.rs", PASSWORD) == ana  # the name ignores case
    assert store.verify("ana.petrovic@firma.rs", "pogresna-lozinka") is None
    assert store.verify("nobody", PASSWORD) is None
    for name, password, role in [
        ("ana.petrovic@firma.rs", PASSWORD, "editor"),  # taken
        ("a", PASSWORD, "editor"),
        ("ana petrovic", PASSWORD, "editor"),
        ("marko", "kratka", "editor"),
        ("marko", PASSWORD, "boss"),
    ]:
        with pytest.raises(UserError):
            store.add(name, password, role)
    assert [u.username for u in store.all()] == ["ana.petrovic@firma.rs"]


def test_there_is_always_an_administrator(store):
    admin, editor = store.by_name("admin"), store.by_name("editor")

    for change in (
        lambda: store.remove(admin.id),
        lambda: store.set_role(admin.id, "editor"),
        lambda: store.set_disabled(admin.id, True),
    ):
        with pytest.raises(UserError, match="only administrator"):
            change()

    store.set_role(editor.id, "admin")  # with a second one, the first may go
    store.remove(admin.id)
    assert store.by_name("admin") is None


# --- signing in --------------------------------------------------------------------------


def test_nothing_but_the_sign_in_page_without_an_account(app):
    visitor = TestClient(app)
    page = visitor.get("/")

    assert page.status_code == 200
    assert "<title>Prijava · Doc Assistant</title>" in page.text
    assert "/static/login.js?v=" in page.text and "/static/app.js" not in page.text
    assert SESSION_COOKIE not in page.cookies  # no session rows for every passer-by
    assert "script-src 'self' 'sha256-" in page.headers["content-security-policy"]
    for path in ("/documents", "/api/config", "/conversations", "/insights"):
        assert visitor.get(path).status_code == 401, path
    assert visitor.post("/ask", json={"question": "Kantina?"}).status_code == 401
    assert visitor.get("/health").status_code == 200  # monitoring stays open


def test_sign_in_and_out(app):
    client = TestClient(app)
    wrong = client.post("/auth/login", json={"username": "editor", "password": "nije-ta-lozinka"})
    assert wrong.status_code == 401 and SESSION_COOKIE not in client.cookies

    client = sign_in(app, "editor")
    assert "<title>Doc Assistant</title>" in client.get("/").text
    config = client.get("/api/config").json()
    assert config["login"] is True
    assert config["user"] == {"username": "editor", "role": "editor"}

    assert client.post("/auth/logout").status_code == 200
    assert client.get("/documents").status_code == 401
    assert "Prijava" in client.get("/").text


def test_sign_in_starts_a_new_session(app):
    """The session id from before the sign-in is never the signed-in one (fixation)."""
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "chosen-by-someone-else", domain="testserver.local")

    client.post("/auth/login", json={"username": "employee", "password": PASSWORD})

    assert client.cookies[SESSION_COOKIE] != "chosen-by-someone-else"
    other = TestClient(app)
    other.cookies.set(SESSION_COOKIE, "chosen-by-someone-else", domain="testserver.local")
    assert other.get("/documents").status_code == 401


def test_wrong_passwords_pause_the_sign_in(app):
    client = TestClient(app)
    wrong = {"username": "Admin", "password": "pogadjam-lozinku"}

    codes = [client.post("/auth/login", json=wrong).status_code for _ in range(6)]

    assert codes == [401] * auth.LOGIN_ATTEMPTS + [429]
    right = {"username": "admin", "password": PASSWORD}
    assert client.post("/auth/login", json=right).status_code == 429  # also the right one
    sign_in(app, "editor")  # other people are not locked out


def test_a_disabled_user_or_a_new_password_is_signed_out_at_once(app, store):
    employee, editor = sign_in(app, "employee"), sign_in(app, "editor")

    store.set_disabled(store.by_name("employee").id, True)
    store.set_password(store.by_name("editor").id, "nova-lozinka-123")

    assert employee.get("/documents").status_code == 401
    assert editor.get("/documents").status_code == 401
    login = {"username": "employee", "password": PASSWORD}
    assert TestClient(app).post("/auth/login", json=login).status_code == 401
    sign_in(app, "editor", "nova-lozinka-123")


def test_a_changed_role_applies_to_the_next_request(app, store):
    client = sign_in(app, "employee")
    assert client.post("/documents/demo").status_code == 403

    store.set_role(store.by_name("employee").id, "editor")

    assert client.post("/documents/demo").status_code == 200


# --- what each role may do ---------------------------------------------------------------

ROUTES = [
    # method, path, the least role that may call it
    ("GET", "/documents", "employee"),
    ("GET", "/conversations", "employee"),
    ("GET", "/documents/progress", "employee"),
    ("POST", "/documents/demo", "editor"),
    ("DELETE", "/documents/unknown-id", "editor"),  # 404 for those who may delete
    ("GET", "/insights", "editor"),
    ("GET", "/queries?kind=gaps", "editor"),
    ("GET", "/reports/gaps", "editor"),
    ("GET", "/queries?kind=recent", "admin"),
    ("GET", "/stats", "admin"),
    ("GET", "/inbox", "admin"),
    ("DELETE", "/queries", "admin"),
    ("DELETE", "/queries/1", "admin"),  # 404 for an administrator
]


@pytest.mark.parametrize("role", users.ROLES)
def test_each_role_reaches_only_its_own_routes(app, role):
    client = sign_in(app, role)

    for method, path, least in ROUTES:
        status = client.request(method, path).status_code
        allowed = users.has_role(role, least)
        assert (status != 403) == allowed, f"{role} {method} {path} -> {status}"
        assert status in (200, 403, 404), f"{role} {method} {path} -> {status}"


def test_the_ui_is_told_what_the_role_may_do(app):
    can = {role: sign_in(app, role).get("/api/config").json()["can"] for role in users.ROLES}

    assert not any(can["employee"].values())
    assert can["editor"] == {
        "edit_documents": True,
        "see_gaps": True,
        "see_usage": False,
        "delete_questions": False,
        "manage_inbox": False,
        "manage_users": False,
    }
    assert all(can["admin"].values())


# --- the administrator's "Korisnici" panel (ADR-049) -------------------------------------


def test_an_administrator_manages_the_users(app, store):
    admin = sign_in(app, "admin")

    listed = admin.get("/users").json()
    assert [(u["username"], u["role"], u["me"]) for u in listed] == [
        ("admin", "admin", True),
        ("editor", "editor", False),
        ("employee", "employee", False),
    ]
    assert "password_hash" not in listed[0] and "password" not in listed[0]

    new = {"username": "Ana", "password": PASSWORD, "role": "employee"}
    created = admin.post("/users", json=new)
    assert created.status_code == 201 and created.json()["username"] == "ana"
    ana = sign_in(app, "ana")
    assert ana.post("/documents/demo").status_code == 403

    ana_id = created.json()["id"]
    assert admin.patch(f"/users/{ana_id}", json={"role": "editor"}).json()["role"] == "editor"
    assert ana.post("/documents/demo").status_code == 200  # at once, without signing in again

    assert admin.patch(f"/users/{ana_id}", json={"password": "nova-lozinka-123"}).status_code == 200
    assert ana.get("/documents").status_code == 401  # signed out everywhere
    ana = sign_in(app, "ana", "nova-lozinka-123")

    assert admin.patch(f"/users/{ana_id}", json={"disabled": True}).json()["disabled"] is True
    assert ana.get("/documents").status_code == 401
    login = {"username": "ana", "password": "nova-lozinka-123"}
    assert TestClient(app).post("/auth/login", json=login).status_code == 401
    assert admin.patch(f"/users/{ana_id}", json={"disabled": False}).json()["disabled"] is False
    sign_in(app, "ana", "nova-lozinka-123")


def test_removing_a_user_removes_their_conversations(app, store):
    upload_faq(app)
    employee = sign_in(app, "employee")
    employee.post("/ask", json={"question": "Kada radi kantina?"})
    employee_id = store.by_name("employee").id
    admin = sign_in(app, "admin")

    removed = admin.delete(f"/users/{employee_id}")

    assert removed.json() == {"conversations_deleted": 1}
    assert employee.get("/conversations").status_code == 401
    assert [u["username"] for u in admin.get("/users").json()] == ["admin", "editor"]
    assert admin.delete(f"/users/{employee_id}").status_code == 404


def test_refused_changes_say_why(app, store):
    admin = sign_in(app, "admin")
    me, editor = store.by_name("admin").id, store.by_name("editor").id

    def code(response):
        assert response.status_code in (404, 422), response.text
        return response.json()["detail"]["code"]

    user = {"username": "marko", "password": PASSWORD}
    assert code(admin.post("/users", json={**user, "username": "editor"})) == "taken"
    assert code(admin.post("/users", json={**user, "username": "x"})) == "username"
    assert code(admin.post("/users", json={**user, "password": "kratka"})) == "password"
    assert code(admin.post("/users", json={**user, "role": "boss"})) == "role"
    assert code(admin.patch("/users/9999", json={"role": "editor"})) == "missing"
    # An administrator cannot lock themselves out of the panel by a slip.
    assert code(admin.patch(f"/users/{me}", json={"role": "editor"})) == "self"
    assert code(admin.patch(f"/users/{me}", json={"disabled": True})) == "self"
    assert code(admin.delete(f"/users/{me}")) == "self"
    assert admin.patch(f"/users/{me}", json={"role": "admin"}).status_code == 200  # no change
    assert [u.username for u in store.all()] == ["admin", "editor", "employee"]
    assert store.get(editor).role == "editor"


@pytest.mark.parametrize("role", ["employee", "editor"])
def test_only_an_administrator_reaches_the_users(app, store, role):
    client = sign_in(app, role)
    target = store.by_name("admin").id
    new = {"username": "marko", "password": PASSWORD, "role": "admin"}

    assert client.get("/users").status_code == 403
    assert client.post("/users", json=new).status_code == 403
    assert client.patch(f"/users/{target}", json={"disabled": True}).status_code == 403
    assert client.delete(f"/users/{target}").status_code == 403
    assert TestClient(app).get("/users").status_code == 401  # not signed in at all
    assert store.by_name("marko") is None and not store.by_name("admin").disabled


def test_an_editor_sees_the_gaps_but_not_the_usage(app):
    upload_faq(app)
    sign_in(app, "employee").post("/ask", json={"question": "Koliki je bonus za Božić?"})

    editor = sign_in(app, "editor").get("/insights").json()
    admin = sign_in(app, "admin").get("/insights").json()

    assert list(editor) == ["gaps"]  # no cost, no list of everyone's questions
    assert set(admin) == {"stats", "recent", "gaps"}
    assert admin["stats"]["queries"] == 1


def test_the_api_key_keeps_full_access_for_integrations(settings, store, tmp_path):
    index = VectorStore(tmp_path / "index", FakeEmbedder())
    app = create_app(
        settings=replace(settings, api_token="n8n-key"),
        store=index,
        llm_factory=lambda s, p: FakeProvider(),
    )
    n8n = TestClient(app, headers={"X-API-Key": "n8n-key"})

    assert n8n.post("/documents/demo").status_code == 200
    assert n8n.get("/reports/gaps").status_code == 200
    assert n8n.get("/stats").status_code == 200
    assert TestClient(app, headers={"X-API-Key": "wrong"}).get("/documents").status_code == 401


# --- conversations and limits ------------------------------------------------------------


def test_conversations_follow_the_person_not_the_browser(app):
    upload_faq(app)
    at_work = sign_in(app, "employee")
    asked = at_work.post("/ask", json={"question": "Kada radi kantina?"}).json()

    at_home = sign_in(app, "employee")  # another browser, the same person
    colleague = sign_in(app, "editor")

    assert [c["id"] for c in at_home.get("/conversations").json()] == [asked["conversation_id"]]
    assert colleague.get("/conversations").json() == []
    assert colleague.get(f"/conversations/{asked['conversation_id']}").status_code == 404


def test_conversations_from_before_accounts_stay_with_whoever_signs_in(settings, store, tmp_path):
    index = VectorStore(tmp_path / "index", FakeEmbedder())
    index_app = {"store": index, "llm_factory": lambda s, p: FakeProvider()}
    before = TestClient(create_app(settings=replace(settings, login_required=False), **index_app))
    before.post("/documents/demo")
    before.get("/")  # the browser gets its session
    old = before.post("/ask", json={"question": "Kada radi kantina?"}).json()["conversation_id"]

    client = TestClient(create_app(settings=settings, **index_app))  # accounts turned on
    client.cookies.set(SESSION_COOKIE, before.cookies[SESSION_COOKIE], domain="testserver.local")
    client.post("/auth/login", json={"username": "employee", "password": PASSWORD})

    assert [c["id"] for c in client.get("/conversations").json()] == [old]
    assert [c["id"] for c in sign_in(client.app, "employee").get("/conversations").json()] == [old]


def test_questions_are_limited_per_person_not_per_address(settings, store, tmp_path):
    index = VectorStore(tmp_path / "index", FakeEmbedder())
    limited = replace(settings, max_questions_per_ip_hour=1, max_questions_per_session=1)
    app = create_app(settings=limited, store=index, llm_factory=lambda s, p: FakeProvider())
    upload_faq(app)
    ask = {"question": "Kada radi kantina?"}
    ana, marko = sign_in(app, "employee"), sign_in(app, "editor")  # one office, one address

    assert ana.post("/ask", json=ask).status_code == 200
    assert ana.post("/ask", json=ask).status_code == 429
    assert sign_in(app, "employee").post("/ask", json=ask).status_code == 429  # a new browser
    assert marko.post("/ask", json=ask).status_code == 200


# --- without accounts, and the command line ----------------------------------------------


def test_without_accounts_everything_is_as_before(settings, tmp_path):
    index = VectorStore(tmp_path / "index", FakeEmbedder())
    open_app = create_app(
        settings=replace(settings, login_required=False),
        store=index,
        llm_factory=lambda s, p: FakeProvider(),
    )
    client = TestClient(open_app)

    assert "<title>Doc Assistant</title>" in client.get("/").text
    config = client.get("/api/config").json()
    assert config["login"] is False and config["user"] is None
    assert config["can"].pop("manage_users") is False  # there are no users to manage
    assert all(config["can"].values())
    assert client.post("/documents/demo").status_code == 200
    assert client.get("/users").status_code == 404
    login = {"username": "admin", "password": PASSWORD}
    assert client.post("/auth/login", json=login).status_code == 404


_from_env = cli.Settings.from_env


def test_users_are_managed_from_the_command_line(settings, monkeypatch, capsys):
    monkeypatch.setattr(cli.Settings, "from_env", lambda: _from_env(load_dotenv_file=False))

    def run(*args, typed=""):
        monkeypatch.setattr("sys.stdin", io.StringIO(typed))
        code = cli.main(["users", *args])
        captured = capsys.readouterr()
        return code, captured.out + captured.err

    assert run("list") == (0, "No users yet. Create the first administrator: "
                           "users add <name> --role admin\n")  # fmt: skip
    code, out = run("add", "Vladimir", "--role", "admin", "--password-stdin", typed=PASSWORD)
    assert code == 0 and "Added vladimir (admin)." in out and "LOGIN_REQUIRED=true" in out
    assert run("add", "vladimir", "--password-stdin", typed=PASSWORD)[0] == 2  # taken
    assert run("add", "ana", "--password-stdin", typed="kratka")[0] == 2
    assert run("add", "ana", "--password-stdin", typed=PASSWORD + "\n")[0] == 0
    assert run("role", "ana", "editor")[0] == 0
    assert run("password", "ana", "--password-stdin", typed="nova-lozinka-123")[0] == 0
    assert run("remove", "vladimir") == (2, "error: This is the only administrator: "
                                            "make another one first\n")  # fmt: skip
    assert run("remove", "nobody")[0] == 2

    store = UserStore(settings.log_db)
    assert [(u.username, u.role) for u in store.all()] == [("ana", "editor"), ("vladimir", "admin")]
    assert store.verify("ana", "nova-lozinka-123") and not store.verify("ana", PASSWORD)
    assert "ana" in run("list")[1]
    assert run("remove", "ana")[0] == 0
