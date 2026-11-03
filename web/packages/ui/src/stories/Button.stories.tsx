import { useState } from "react";
import { Button } from "../components/Button";
export default { title: "Components/Button" };
export const Keyboard = {
  render: () => {
    const [count, setCount] = useState(0);
    return (
      <>
        <Button onClick={() => setCount(count + 1)}>Compare employers</Button>
        <output aria-label="Selected count">{count}</output>
        <Button disabled>Unavailable</Button>
      </>
    );
  },
};
export const Dark = { ...Keyboard, parameters: { theme: "dark" } };
