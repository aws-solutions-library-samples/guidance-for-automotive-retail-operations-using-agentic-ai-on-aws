import View from "./View";

// F&I deal worksheet (§5.13). Static export: generated once with the "_"
// placeholder; CloudFront rewrites /dealer/fni/<deal or journey id> here.
export function generateStaticParams() {
  return [{ id: "_" }];
}

export default function Page() {
  return <View />;
}
