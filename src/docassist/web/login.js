// The sign-in page (ADR-048): POST /auth/login, then the app loads at the same address.

const form = document.querySelector("#login-form");
const error = document.querySelector("#login-error");
const submit = document.querySelector("#login-submit");

const MESSAGES = {
  401: "Pogrešno korisničko ime ili lozinka.",
  422: "Upiši korisničko ime i lozinku.",
  429: "Previše pogrešnih pokušaja. Probaj ponovo za 15 minuta.",
};

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.hidden = true;
  submit.disabled = true;
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: form.username.value, password: form.password.value }),
    });
    if (response.ok) {
      location.replace("/");
      return;
    }
    error.textContent = MESSAGES[response.status] || `Prijava nije uspela (${response.status}).`;
  } catch {
    error.textContent = "Server nije dostupan. Probaj ponovo.";
  }
  error.hidden = false;
  submit.disabled = false;
  form.password.select();
});
