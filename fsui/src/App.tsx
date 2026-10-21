import type { QueryClient } from "@tanstack/react-query";
import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { useAuthOptions } from "@/api/hooks";
import { AuthProvider, useAuth } from "@/auth/AuthContext";
import { isClientUser } from "@/auth/permissions";
import { PrefsProvider } from "@/components/Prefs";
import { StepUpProvider } from "@/components/StepUp";
import { ToastProvider } from "@/components/Toast";
import { Spinner } from "@/components/ui";
import { AppShell } from "@/layout/AppShell";
import { AdminPage } from "@/pages/admin/AdminPages";
import { AcceptInvitePage, ChangePasswordPage, ForgotPasswordPage, LoginPage, MfaEnrollPage, MfaPage, ResetPasswordPage } from "@/pages/auth/AuthPages";
import { SetupPage } from "@/pages/auth/SetupPage";
import { SignupPage } from "@/pages/auth/SignupPage";
import { ClientDetailPage, ClientsPage } from "@/pages/clients/ClientsPages";
import { Dashboard } from "@/pages/Dashboard";
import { Workspace } from "@/pages/engagement/Workspace";
import { AuditSearchPage, HelpPage, NotFoundPage, ProfilePage, ReleaseNotesPage, RulesPage } from "@/pages/OtherPages";
import { PortalEngagement, PortalHome } from "@/pages/portal/PortalPages";

function Home() {
  const { me } = useAuth();
  return isClientUser(me) ? <Navigate to="/portal" replace /> : <Dashboard />;
}

function ScrollTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    // reset scroll per page; tabs inside the workspace keep their position
    if (pathname.startsWith("/engagements/")) return;
    try {
      window.scrollTo(0, 0);
    } catch {
      // not available (tests)
    }
  }, [pathname]);
  return null;
}

/** A fresh installation (no firm yet) starts at the first-run setup; the launcher's one-time token rides in the hash. */
function SetupGate({ children }: { children: ReactNode }) {
  const options = useAuthOptions();
  const { pathname, hash } = useLocation();
  if (options.isLoading) {
    return (
      <div className="auth-wrap">
        <Spinner label="Starting FS-Platform" />
      </div>
    );
  }
  if (options.data?.setup_required && pathname !== "/setup") return <Navigate to={{ pathname: "/setup", hash }} replace />;
  return <>{children}</>;
}

export function AppRoutes() {
  return (
    <SetupGate>
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/mfa" element={<MfaPage />} />
        <Route path="/mfa/enroll" element={<MfaEnrollPage />} />
        <Route path="/change-password" element={<ChangePasswordPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/invite" element={<AcceptInvitePage />} />
        <Route element={<AppShell />}>
          <Route index element={<Home />} />
          <Route path="/clients" element={<ClientsPage />} />
          <Route path="/clients/:clientId" element={<ClientDetailPage />} />
          <Route path="/engagements/:engagementId" element={<Workspace />} />
          <Route path="/engagements/:engagementId/:tab" element={<Workspace />} />
          <Route path="/portal" element={<PortalHome />} />
          <Route path="/portal/engagements/:engagementId" element={<PortalEngagement />} />
          <Route path="/audit" element={<AuditSearchPage />} />
          <Route path="/rules" element={<RulesPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/admin/:section" element={<AdminPage />} />
          <Route path="/profile" element={<ProfilePage />} />
          <Route path="/help" element={<HelpPage />} />
          <Route path="/release-notes" element={<ReleaseNotesPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </SetupGate>
  );
}

export function App({ client }: { client: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <PrefsProvider>
        <ToastProvider>
          <AuthProvider>
            <StepUpProvider>
              <ScrollTop />
              <AppRoutes />
            </StepUpProvider>
          </AuthProvider>
        </ToastProvider>
      </PrefsProvider>
    </QueryClientProvider>
  );
}
