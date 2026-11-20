import { Switch, Route } from "wouter";
import OperationsHub from "@/pages/operations-hub";

function NotFound() {
  return (
    <div className="flex h-screen w-full items-center justify-center">
      <p className="text-muted-foreground">404 — Page not found</p>
    </div>
  );
}

export default function App() {
  return (
    <Switch>
      <Route path="/" component={OperationsHub} />
      <Route component={NotFound} />
    </Switch>
  );
}
