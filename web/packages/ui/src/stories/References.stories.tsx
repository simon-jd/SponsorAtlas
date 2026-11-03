import { useState, type ReactNode } from "react";
import {
  Button,
  ChartFrame,
  Combobox,
  DataTable,
  MapContainer,
  Tabs,
  Tooltip,
} from "../index";
import "./references.css";
const disclaimer =
  "LCA applications are not approvals and do not mean a position necessarily offers sponsorship.";
type Employer = { id: string; name: string; filings: number; wage: string };
const employers: Employer[] = [
  { id: "atlas", name: "Atlas Research", filings: 42, wage: "$128,000" },
  { id: "cedar", name: "Cedar Labs", filings: 18, wage: "$116,000" },
];
const columns = [
  {
    key: "name",
    header: "Employer",
    render: (row: Employer) => row.name,
    compare: (a: Employer, b: Employer) => a.name.localeCompare(b.name),
  },
  {
    key: "filings",
    header: "LCA filings",
    render: (row: Employer) => row.filings,
    compare: (a: Employer, b: Employer) => a.filings - b.filings,
  },
  {
    key: "wage",
    header: "Median reported annual wage",
    render: (row: Employer) => row.wage,
  },
];
function Shell({
  title,
  eyebrow,
  children,
}: {
  title: string;
  eyebrow: string;
  children: ReactNode;
}) {
  return (
    <div className="reference">
      <header>
        <strong>SponsorAtlas</strong>
        <span>Public disclosure explorer</span>
        <span className="reference-note">
          Local design reference · illustrative data
        </span>
      </header>
      <div className="reference-heading">
        <p>{eyebrow}</p>
        <h1>{title}</h1>
        <p>Read the evidence. Understand the limits.</p>
      </div>
      {children}
      <footer>
        {disclaimer}
        <p>
          Illustrative FY2025 values. Production pages must link each metric to
          its disclosure release and methodology.
        </p>
      </footer>
    </div>
  );
}
function Chart() {
  return (
    <ChartFrame
      title="Filing activity"
      description="Annual public filings. Illustrative counts."
      dataLabel="View chart data"
      data={
        <table>
          <caption>Annual filings</caption>
          <tbody>
            <tr>
              <th scope="row">2024</th>
              <td>36</td>
            </tr>
            <tr>
              <th scope="row">2025</th>
              <td>42</td>
            </tr>
          </tbody>
        </table>
      }
    >
      <div className="reference-bars" aria-hidden="true">
        <span style={{ height: "70%" }}>2024 · 36</span>
        <span style={{ height: "85%" }}>2025 · 42</span>
      </div>
    </ChartFrame>
  );
}
function ExploreView() {
  const [value, setValue] = useState("");
  return (
    <Shell title="Explore employer filings" eyebrow="Explore / United States">
      <div className="reference-filters">
        <Combobox
          label="Find an employer"
          options={employers.map((row) => ({ value: row.id, label: row.name }))}
          value={value}
          onValueChange={setValue}
          emptyMessage="No matching employers"
          placeholder="Search public employer names"
        />
        <p>Fiscal year: 2025 · All occupations</p>
        <Tooltip label="What counts as a filing?" content={disclaimer} />
      </div>
      <div className="reference-metrics">
        <div>
          <span>Employers in this example</span>
          <strong>2</strong>
        </div>
        <div>
          <span>Reported LCA filings</span>
          <strong>60</strong>
        </div>
        <div>
          <span>Disclosure period</span>
          <strong>FY2025</strong>
        </div>
      </div>
      <DataTable
        caption="Employer results"
        columns={columns}
        rows={value ? employers.filter((row) => row.id === value) : employers}
        rowKey={(row) => row.id}
        emptyMessage="No results for these filters"
      />
      <div className="reference-grid">
        <Chart />
        <MapContainer
          title="Regional filing distribution"
          description="Aggregated geography only; individual worksites are not plotted."
          status="Map unavailable in this reference"
          fallbackLabel="View region list"
          fallback={
            <ul>
              <li>California: 42 filings</li>
              <li>Washington: 18 filings</li>
            </ul>
          }
        />
      </div>
    </Shell>
  );
}
function EmployerView() {
  return (
    <Shell title="Atlas Research" eyebrow="Explore / Employer details">
      <p>Illustrative employer · Research services</p>
      <Button disabled>Add to comparison</Button>
      <div className="reference-metrics">
        <div>
          <span>LCA filings</span>
          <strong>42</strong>
        </div>
        <div>
          <span>Median reported annual wage</span>
          <strong>$128,000</strong>
        </div>
        <div>
          <span>Release</span>
          <strong>FY2025</strong>
        </div>
      </div>
      <Tabs
        label="Employer evidence"
        items={[
          {
            id: "overview",
            label: "Overview",
            content: (
              <div className="reference-grid">
                <Chart />
                <section className="sa-frame">
                  <h2>How to read this profile</h2>
                  <p>{disclaimer}</p>
                  <p>
                    Counts describe applications in public disclosure data. Wage
                    values reflect reported amounts and annualization
                    methodology.
                  </p>
                </section>
              </div>
            ),
          },
          {
            id: "wages",
            label: "Wages",
            content: (
              <p>No occupation-level wage breakdown in this reference.</p>
            ),
          },
          {
            id: "sources",
            label: "Sources & methodology",
            content: (
              <p>
                Local reference data only. Production evidence must identify
                archived source, release, hash, and model lineage.
              </p>
            ),
          },
        ]}
      />
    </Shell>
  );
}
function CompareView() {
  return (
    <Shell title="Compare employer evidence" eyebrow="Explore / Compare">
      <p>Two illustrative employers · FY2025 · Same disclosure period</p>
      <DataTable
        caption="Employer comparison"
        columns={columns}
        rows={employers}
        rowKey={(row) => row.id}
        emptyMessage="Choose employers to compare"
      />
      <div className="reference-grid">
        <Chart />
        <section className="sa-frame">
          <h2>Comparison limits</h2>
          <p>
            A higher filing count does not guarantee sponsorship or approval.
          </p>
          <p>
            Compare the same year, occupation, and wage basis. Missing values
            should display as unavailable, never as zero.
          </p>
        </section>
      </div>
    </Shell>
  );
}
export default { title: "Design/References" };
export const Explore = { render: () => <ExploreView /> };
export const Employer = { render: () => <EmployerView /> };
export const Compare = {
  render: () => <CompareView />,
  parameters: { theme: "dark" },
};
export const Narrow = { render: () => <ExploreView /> };
