import { DataTable } from "../components/DataTable";
const columns = [
  {
    key: "name",
    header: "Employer",
    render: (r: Row) => r.name,
    compare: (a: Row, b: Row) => a.name.localeCompare(b.name),
  },
  {
    key: "count",
    header: "LCA filings",
    render: (r: Row) => r.count,
    compare: (a: Row, b: Row) => a.count - b.count,
  },
];
type Row = { id: string; name: string; count: number };
const rows = [
  { id: "c", name: "Cedar Labs", count: 18 },
  { id: "a", name: "Atlas Research", count: 42 },
];
export default { title: "Components/DataTable" };
export const Keyboard = {
  render: () => (
    <DataTable
      caption="Employer filings · illustrative data"
      columns={columns}
      rows={rows}
      rowKey={(r: Row) => r.id}
      emptyMessage="No filings for these filters"
    />
  ),
};
export const Empty = {
  render: () => (
    <DataTable
      caption="Employer filings"
      columns={columns}
      rows={[]}
      rowKey={(r: Row) => r.id}
      emptyMessage="No filings for these filters"
    />
  ),
};
export const Dark = { ...Keyboard, parameters: { theme: "dark" } };
export const Narrow = {
  render: () => (
    <DataTable
      caption="Employer filings · illustrative data"
      columns={[
        ...columns,
        {
          key: "basis",
          header: "Disclosure period and wage comparison basis",
          render: () => "FY2025 · Annual reported wage",
        },
      ]}
      rows={rows}
      rowKey={(row: Row) => row.id}
      emptyMessage="No filings for these filters"
    />
  ),
};
