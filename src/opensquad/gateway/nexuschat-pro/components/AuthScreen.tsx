import React, { useState, useEffect } from 'react';
import { User as UserIcon, Lock, ArrowRight, Mail } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { setLanguage } from '../i18n';

interface AuthScreenProps {
  onLogin: (email: string, password: string) => Promise<void>;
  onRegister: (name: string, email: string, password: string) => Promise<void>;
  /**
   * True on a fresh deployment where no web account exists yet — the form
   * opens in sign-up mode and the "switch to login" link is hidden (no
   * point telling the user to log in to a system with no users).
   */
  isFirstTime?: boolean;
  /**
   * True when a web account already exists, or when the status probe
   * failed. The form is locked to sign-in mode and the "switch to sign
   * up" link is hidden (the backend would 403 a second registration
   * anyway).
   */
  registrationClosed?: boolean;
}

export const AuthScreen: React.FC<AuthScreenProps> = ({
  onLogin,
  onRegister,
  isFirstTime = false,
  registrationClosed = false,
}) => {
  const { t, i18n } = useTranslation();
  // First-time flow forces sign-up mode; registration-closed flow forces
  // sign-in mode. The setter is exposed so the (rare) "neither" case
  // — a system that allows manual account creation but isn't fresh — can
  // still let the user toggle between the two.
  const [isLogin, setIsLogin] = useState<boolean>(!isFirstTime);

  // When the first-time / closed flags change after mount (e.g. the
  // status endpoint resolves late on a refresh), keep the form in sync
  // with what the parent knows about the backend state.
  useEffect(() => {
    if (isFirstTime) setIsLogin(false);
    else if (registrationClosed) setIsLogin(true);
  }, [isFirstTime, registrationClosed]);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string>('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsSubmitting(true);

    try {
      if (isLogin) {
        await onLogin(email, password);
      } else {
        if (!name.trim()) {
          setError(t('auth.nameRequired'));
          setIsSubmitting(false);
          return;
        }
        await onRegister(name, email, password);
      }
    } catch (err: any) {
      const rawMsg = (err && typeof err.message === 'string') ? err.message : '';
      // Map server-side "Registration closed" to a localized message so the
      // error banner is not language-mixed (backend default is English).
      if (/registration closed/i.test(rawMsg)) {
        setError(t('auth.registrationClosed'));
      } else {
        setError(rawMsg && rawMsg.trim() ? rawMsg : t('auth.authFailed'));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="h-full w-full bg-bgLight flex items-center justify-center font-sans overflow-hidden relative">
      {/* Language Switch — bottom-left corner */}
      <button
        onClick={() => setLanguage(i18n.language === 'zh' ? 'en' : 'zh')}
        className="fixed bottom-4 left-4 z-10 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-textMuted bg-panel/80 backdrop-blur border border-border/40 hover:border-primary/40 hover:text-primary transition-all"
      >
        {i18n.language === 'zh' ? 'EN' : '中文'}
      </button>
      <div className="bg-panel p-6 sm:p-8 rounded-2xl shadow-xl w-full max-w-md mx-4 border border-border animate-in fade-in slide-in-from-bottom-4 duration-500 max-h-[90dvh] overflow-y-auto">
        <div className="flex flex-col items-center mb-6">
          <img
            src="/logo.svg"
            alt="OpenSquad"
            className="w-12 h-12 mb-4 drop-shadow-lg"
          />
          <h1 className="text-2xl font-bold text-textMain">{isLogin ? t('auth.welcomeBack') : t('auth.createAccount')}</h1>
          <p className="text-textMuted text-sm mt-1">{t('auth.subtitle')}</p>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-600 text-sm">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name field for Sign Up */}
          {!isLogin && (
            <div className="space-y-1">
              <label className="text-xs font-semibold text-textMuted ml-1">{t('auth.nameLabel')}</label>
              <div className="relative">
                <UserIcon className="absolute left-3 top-3 text-textMuted" size={18} />
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full pl-10 pr-4 py-2.5 bg-bgLight border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/50 transition-all text-sm text-textMain placeholder:text-textMuted/50"
                  placeholder={t('auth.namePlaceholder')}
                  required={!isLogin}
                />
              </div>
            </div>
          )}

          <div className="space-y-1">
            <label className="text-xs font-semibold text-textMuted ml-1">{t('auth.emailLabel')}</label>
            <div className="relative">
              <Mail className="absolute left-3 top-3 text-textMuted" size={18} />
              <input
                type="email"
                data-testid="auth-email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full pl-10 pr-4 py-2.5 bg-bgLight border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/50 transition-all text-sm text-textMain placeholder:text-textMuted/50"
                placeholder={t('auth.emailPlaceholder')}
                required
                autoFocus
              />
            </div>
          </div>

          <div className="space-y-1">
            <label className="text-xs font-semibold text-textMuted ml-1">{t('auth.passwordLabel')}</label>
            <div className="relative">
              <Lock className="absolute left-3 top-3 text-textMuted" size={18} />
              <input
                type="password"
                data-testid="auth-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full pl-10 pr-4 py-2.5 bg-bgLight border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/50 transition-all text-sm text-textMain placeholder:text-textMuted/50"
                placeholder={t('auth.passwordPlaceholder')}
                required
                minLength={6}
              />
            </div>
          </div>

          <button
            type="submit"
            data-testid="auth-submit"
            disabled={isSubmitting}
            className="w-full py-3 bg-primary hover:bg-primary/90 text-white rounded-xl font-semibold shadow-md hover:shadow-lg transition-all flex items-center justify-center gap-2 mt-2 disabled:opacity-70 disabled:cursor-not-allowed"
          >
            {isSubmitting ? t('auth.processing') : (isLogin ? t('auth.signIn') : t('auth.register'))} <ArrowRight size={18} />
          </button>
        </form>

        {/* Mode toggle: hidden when the backend state forces one direction
            (first-time forces sign-up, registration-closed forces sign-in). */}
        {!isFirstTime && !registrationClosed && (
          <div className="mt-6 text-center">
            <p className="text-sm text-textMuted">
              {isLogin ? t('auth.noAccount') : t('auth.hasAccount')}
              <button
                onClick={() => {
                  setIsLogin(!isLogin);
                  setError('');
                }}
                className="text-primary font-semibold ml-1 hover:underline focus:outline-none"
              >
                {isLogin ? t('auth.signUp') : t('auth.logIn')}
              </button>
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
