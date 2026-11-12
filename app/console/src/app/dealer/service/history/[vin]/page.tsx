// Static export: one placeholder page per dynamic route; the client view reads
// the real VIN from the path (CloudFront rewrites /dealer/service/history/<vin> here).
import View from "./View";

export function generateStaticParams() {
  return [{ vin: "_" }];
}

export default function Page() {
  return <View />;
}
