// Static export: one placeholder page per dynamic route; the client view reads
// the real id from the path (CloudFront rewrites /dealer/service/<id>/... here).
import View from "./View";

export function generateStaticParams() {
  return [{ id: "_" }];
}

export default function Page() {
  return <View />;
}
