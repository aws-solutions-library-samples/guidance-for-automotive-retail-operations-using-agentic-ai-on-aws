import View from "./View";

// Static export: one placeholder page; CloudFront maps /oem/accounts/<id> here
// and the client view reads the real id from the path.
export function generateStaticParams() {
  return [{ id: "_" }];
}

export default function Page() {
  return <View />;
}
