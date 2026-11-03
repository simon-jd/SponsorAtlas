import { ChartFrame } from "../components/ChartFrame";
export default { title: "Components/ChartFrame" };
export const Keyboard = {
  render: () => (
    <ChartFrame
      title="Annual LCA filings"
      description="Illustrative totals, not approvals."
      dataLabel="View chart data"
      data={
        <table>
          <caption>Filings by year</caption>
          <tbody>
            <tr>
              <th scope="row">2025</th>
              <td>42</td>
            </tr>
          </tbody>
        </table>
      }
    >
      <p>Chart preview: 42 filings in 2025</p>
    </ChartFrame>
  ),
};
export const Unavailable = {
  render: () => (
    <ChartFrame
      title="Annual LCA filings"
      description="Source unavailable"
      dataLabel="View chart data"
      data={<p>No data available</p>}
      status="Chart unavailable"
    />
  ),
};
export const Dark = { ...Keyboard, parameters: { theme: "dark" } };
