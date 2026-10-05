import { Suspense } from "react";
import { CustomerView } from "./customer-view";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Suspense>
      <CustomerView id={decodeURIComponent(id)} />
    </Suspense>
  );
}
