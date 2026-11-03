import { MapContainer } from "../components/MapContainer";
export default { title: "Components/MapContainer" };
export const Keyboard = {
  render: () => (
    <MapContainer
      title="Filings by region"
      description="Aggregated public filing counts."
      fallbackLabel="View region list"
      fallback={
        <ul>
          <li>California: 42 filings</li>
        </ul>
      }
    >
      <p>Map preview reserved for aggregated geometry</p>
    </MapContainer>
  ),
};
export const Unavailable = {
  render: () => (
    <MapContainer
      title="Filings by region"
      description="Aggregated public filing counts."
      fallbackLabel="View region list"
      fallback={<p>No region data available</p>}
      status="Map unavailable"
    />
  ),
};
export const Dark = { ...Keyboard, parameters: { theme: "dark" } };
