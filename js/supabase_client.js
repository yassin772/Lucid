(function () {
  const config = window.LUCID_SUPABASE_CONFIG || {};
  const metaUrl = document.querySelector('meta[name="supabase-url"]')?.content || "";
  const metaAnonKey = document.querySelector('meta[name="supabase-anon-key"]')?.content || "";
  const localUrl = window.localStorage?.getItem("LUCID_SUPABASE_URL") || "";
  const localAnonKey = window.localStorage?.getItem("LUCID_SUPABASE_ANON_KEY") || "";

  const url = config.url || config.SUPABASE_URL || metaUrl || localUrl;
  const anonKey = config.anonKey || config.SUPABASE_ANON_KEY || metaAnonKey || localAnonKey;

  function isConfigured() {
    return Boolean(url && anonKey && window.supabase?.createClient);
  }

  function createClient() {
    if (!isConfigured()) return null;
    return window.supabase.createClient(url, anonKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    });
  }

  const client = createClient();

  async function getSession() {
    if (!client) return { data: { session: null }, error: null };
    return client.auth.getSession();
  }

  async function signUp(email, password) {
    if (!client) throw new Error("Supabase is not configured.");
    return client.auth.signUp({
      email,
      password,
      options: {
        emailRedirectTo: new URL("auth.html?mode=callback", window.location.href).toString(),
      },
    });
  }

  async function signIn(email, password) {
    if (!client) throw new Error("Supabase is not configured.");
    return client.auth.signInWithPassword({ email, password });
  }

  async function sendPasswordReset(email) {
    if (!client) throw new Error("Supabase is not configured.");
    return client.auth.resetPasswordForEmail(email, {
      redirectTo: new URL("auth.html?mode=reset", window.location.href).toString(),
    });
  }

  async function updatePassword(password) {
    if (!client) throw new Error("Supabase is not configured.");
    return client.auth.updateUser({ password });
  }

  async function signOut() {
    if (!client) return;
    await client.auth.signOut();
  }

  async function getProfile(user) {
    if (!client || !user) return { profile: null, error: null };
    const diagnostics = {
      userId: user.id,
      rpcAttempted: false,
      rpcError: null,
      directAttempted: false,
      directError: null,
      fallbackInsertAttempted: false,
      fallbackInsertError: null,
    };

    diagnostics.rpcAttempted = true;
    const rpcResult = await client.rpc("get_my_profile");
    if (rpcResult.data) {
      return { profile: Array.isArray(rpcResult.data) ? rpcResult.data[0] : rpcResult.data, error: null, diagnostics };
    }
    if (rpcResult.error) diagnostics.rpcError = {
      code: rpcResult.error.code,
      message: rpcResult.error.message,
      details: rpcResult.error.details,
      hint: rpcResult.error.hint,
    };
    if (rpcResult.error && !["PGRST202", "42883"].includes(rpcResult.error.code)) {
      return { profile: null, error: rpcResult.error, diagnostics };
    }

    diagnostics.directAttempted = true;
    const { data, error } = await client
      .from("profiles")
      .select("*")
      .eq("id", user.id)
      .maybeSingle();

    if (data) return { profile: data, error: null, diagnostics };

    if (error) diagnostics.directError = {
      code: error.code,
      message: error.message,
      details: error.details,
      hint: error.hint,
    };

    if (error && error.code !== "PGRST116") return { profile: data, error, diagnostics };

    const fallbackProfile = {
      id: user.id,
      email: user.email,
      subscription_status: "trial",
    };
    diagnostics.fallbackInsertAttempted = true;
    const inserted = await client
      .from("profiles")
      .insert(fallbackProfile)
      .select("*")
      .single();

    if (inserted.error) diagnostics.fallbackInsertError = {
      code: inserted.error.code,
      message: inserted.error.message,
      details: inserted.error.details,
      hint: inserted.error.hint,
    };

    return { profile: inserted.data, error: inserted.error, diagnostics };
  }

  async function markExpired(profile) {
    if (!client || !profile || profile.subscription_status !== "trial") return;
    await client
      .from("profiles")
      .update({ subscription_status: "expired" })
      .eq("id", profile.id);
  }

  function hasAccess(profile) {
    if (!profile) return false;
    if (profile.subscription_status === "active") return true;
    if (profile.subscription_status !== "trial") return false;
    if (!profile.trial_end) return false;
    return new Date(profile.trial_end).getTime() > Date.now();
  }

  window.LucidSupabase = {
    client,
    url,
    anonKeyPresent: Boolean(anonKey),
    isConfigured,
    getSession,
    signUp,
    signIn,
    sendPasswordReset,
    updatePassword,
    signOut,
    getProfile,
    markExpired,
    hasAccess,
  };
})();
