import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import Login from "./Login";

function renderLogin() {
  return render(
    <AuthProvider>
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe("Login page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the server's error message on failed login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ error: { code: "invalid_credentials", message: "Invalid email/username or password." } }),
      }),
    );
    renderLogin();
    fireEvent.change(screen.getByLabelText(/email or username/i), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: "bad" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByText("Invalid email/username or password.")).toBeInTheDocument();
  });

  it("sends identifier and password to the login endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({ access_token: "t", token_type: "bearer", user: { id: 1, email: "a@b.c", username: "admin", role: "admin" } }),
    });
    vi.stubGlobal("fetch", fetchMock);
    renderLogin();
    fireEvent.change(screen.getByLabelText(/email or username/i), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: "Test@123" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/auth/login");
    expect(JSON.parse(init.body)).toEqual({ identifier: "admin", password: "Test@123" });
  });
});
