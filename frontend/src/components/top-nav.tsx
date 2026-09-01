import { Link, useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";

export function TopNav() {
  const location = useLocation();
  const currentPath = location.pathname;

  return (
    <nav className="flex flex-wrap items-center justify-between gap-3 rounded-3xl border border-border/70 bg-card/90 px-4 py-3 shadow-lg backdrop-blur md:px-6">
      <div className="flex flex-wrap items-center gap-2">
       
        <Button asChild variant={currentPath === "/story-video" ? "secondary" : "ghost"}>
          <Link to="/story-video">Story Video</Link>
        </Button>
      </div>
    </nav>
  );
}

