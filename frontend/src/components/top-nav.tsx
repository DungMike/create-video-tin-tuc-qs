import { Link, useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";

export function TopNav() {
  const location = useLocation();
  const currentPath = location.pathname;

  return (
    <nav className="flex flex-wrap items-center justify-between gap-3 rounded-3xl border border-border/70 bg-card/90 px-4 py-3 shadow-lg backdrop-blur md:px-6">
      <Link to="/" className="text-sm font-semibold tracking-tight text-foreground whitespace-nowrap">
        Auto Video Review Studio
      </Link>
      <div className="flex flex-wrap items-center gap-2">
        <Button asChild variant={currentPath === "/" ? "secondary" : "ghost"}>
          <Link to="/">Upload</Link>
        </Button>
        <Button asChild variant={currentPath === "/docs-to-audio" ? "secondary" : "ghost"}>
          <Link to="/docs-to-audio">Docs to Audio</Link>
        </Button>
        <Button asChild variant={currentPath === "/batch-pipeline" ? "secondary" : "ghost"}>
          <Link to="/batch-pipeline">Batch Pipeline</Link>
        </Button>
        <Button asChild variant={currentPath.startsWith("/news-bulletin") ? "secondary" : "ghost"}>
          <Link to="/news-bulletin">News Bulletin</Link>
        </Button>
        <Button asChild variant={currentPath === "/channels" ? "secondary" : "ghost"}>
          <Link to="/channels">Channels</Link>
        </Button>
        <Button asChild variant={currentPath === "/voices" ? "secondary" : "ghost"}>
          <Link to="/voices">Voices</Link>
        </Button>
        <Button asChild variant={currentPath === "/decor-library" ? "secondary" : "ghost"}>
          <Link to="/decor-library">Decor Library</Link>
        </Button>
        <Button asChild variant={currentPath === "/effects-library" ? "secondary" : "ghost"}>
          <Link to="/effects-library">Effects Library</Link>
        </Button>
        <Button asChild variant={currentPath === "/story-video" ? "secondary" : "ghost"}>
          <Link to="/story-video">Story Video</Link>
        </Button>
      </div>
    </nav>
  );
}

