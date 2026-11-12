import ServiceVisitView from "./View";

// Static export: one placeholder page; CloudFront maps /garage/service/<id>
// here and the client reads the real id from the path (useRouteId).
export function generateStaticParams() {
  return [{ id: "_" }];
}

export default function Page() {
  return <ServiceVisitView />;
}
