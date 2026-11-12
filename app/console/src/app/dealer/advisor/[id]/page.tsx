import AdvisorView from "./View";

// Static export: one placeholder page; CloudFront maps /dealer/advisor/<id> here
// and the client view reads the real journey id from the path.
export function generateStaticParams() {
  return [{ id: "_" }];
}

export default function Page() {
  return <AdvisorView />;
}
