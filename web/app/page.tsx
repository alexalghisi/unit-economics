import { Suspense } from "react";
import { Dashboard } from "./dashboard";

export default function Page() {
  return (
    <Suspense>
      <Dashboard />
    </Suspense>
  );
}
