import { Suspense } from "react";
import { Simulator } from "./simulator";

export default function Page() {
  return (
    <Suspense>
      <Simulator />
    </Suspense>
  );
}
