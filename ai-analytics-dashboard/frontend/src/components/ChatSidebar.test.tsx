import { fireEvent, render, screen } from "@testing-library/react";
import ChatSidebar from "./ChatSidebar";

const chats = [
  { id: 2, title: "Revenue by region", updated_at: "2026-10-17T10:00:00Z", turn_count: 2 },
  { id: 1, title: "Salary by department", updated_at: "2026-10-16T10:00:00Z", turn_count: 1 },
];

function setup(over = {}) {
  const props = { chats, activeId: null, busy: false, onSelect: vi.fn(), onDelete: vi.fn(), onNew: vi.fn(), onLoadMore: vi.fn(), ...over };
  render(<ChatSidebar {...props} />);
  return props;
}

describe("ChatSidebar", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists chats and selects one", () => {
    const p = setup();
    fireEvent.click(screen.getByText("Salary by department"));
    expect(p.onSelect).toHaveBeenCalledWith(1);
  });

  it("starts a new conversation", () => {
    const p = setup();
    fireEvent.click(screen.getByRole("button", { name: /new conversation/i }));
    expect(p.onNew).toHaveBeenCalled();
  });

  it("deletes only after confirmation", () => {
    const p = setup();
    vi.stubGlobal("confirm", vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true));
    const del = screen.getByRole("button", { name: /delete revenue by region/i });
    fireEvent.click(del);
    expect(p.onDelete).not.toHaveBeenCalled();
    fireEvent.click(del);
    expect(p.onDelete).toHaveBeenCalledWith(2);
  });

  it("disables everything while a question is in flight", () => {
    const p = setup({ busy: true });
    fireEvent.click(screen.getByText("Revenue by region"));
    expect(p.onSelect).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /new conversation/i })).toBeDisabled();
  });

  it("shows each chat's formatted date", () => {
    setup();
    expect(screen.getByText(new Date(chats[0].updated_at).toLocaleDateString())).toBeInTheDocument();
    expect(screen.getByText(new Date(chats[1].updated_at).toLocaleDateString())).toBeInTheDocument();
  });

  it("shows an empty state", () => {
    setup({ chats: [] });
    expect(screen.getByText(/no saved conversations/i)).toBeInTheDocument();
  });

  it("offers Load more only while more chats exist", () => {
    const p = setup({ total: 5 });
    fireEvent.click(screen.getByRole("button", { name: /load more/i }));
    expect(p.onLoadMore).toHaveBeenCalled();
  });

  it("hides Load more when everything is loaded", () => {
    setup({ total: 2 });
    expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
  });

  it("shows an error instead of the empty state when the list failed", () => {
    setup({ chats: [], error: true });
    expect(screen.getByText(/could not load conversations/i)).toBeInTheDocument();
    expect(screen.queryByText(/no saved conversations/i)).not.toBeInTheDocument();
  });
});
