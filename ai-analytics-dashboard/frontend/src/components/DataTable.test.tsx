import { fireEvent, render, screen, within } from "@testing-library/react";
import DataTable from "./DataTable";

describe("DataTable", () => {
  it("renders rows and sorts by a column when its header is clicked", () => {
    render(<DataTable columns={["region", "total"]} rows={[["East", 10], ["West", 30], ["North", 20]]} />);
    const firstCell = () => within(screen.getAllByRole("row")[1]).getAllByRole("cell")[0].textContent;
    expect(firstCell()).toBe("East");
    fireEvent.click(screen.getByRole("button", { name: /total/ }));
    expect(firstCell()).toBe("East");
    fireEvent.click(screen.getByRole("button", { name: /total/ }));
    expect(firstCell()).toBe("West");
  });

  it("shows an empty state", () => {
    render(<DataTable columns={["a"]} rows={[]} />);
    expect(screen.getByText(/no rows/i)).toBeInTheDocument();
  });
});
