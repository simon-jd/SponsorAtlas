// Storybook10.6's TS loader calls module.register(), disallowed inside Jest30.
// Load the plain CJS hooks through Jest's supported setup extension instead.
import { setPreVisit, setPostVisit } from "@storybook/test-runner";
import hooks from "./browser-contract.cjs";
setPreVisit(hooks.preVisit);
setPostVisit(hooks.postVisit);
