import CustomerView from "./View";

// Static export: one placeholder page; CloudFront maps /dealer/customers/<id> here.
export function generateStaticParams() {
  return [{ id: "_" }];
}

export default function Page() {
  return <CustomerView />;
}
